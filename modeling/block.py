# encoding: utf-8
"""
@author:  liaoxingyu
@contact: sherlockliao01@gmail.com
"""

import torch
from torch import nn
import torch.nn.functional as F
import numpy as np
import cv2, os
from torchvision.transforms import ToPILImage
from .backbones.resnet import ResNet, BasicBlock, Bottleneck
from .backbones.senet import SENet, SEResNetBottleneck, SEBottleneck, SEResNeXtBottleneck
from .backbones.alexnet import alexnet
from .backbones.vgg2 import vgg11_bn
from .backbones.densenet import densenet121

# torch.cuda.manual_seed_all(1)
# torch.manual_seed(1)
# np.random.seed(1)


def weights_init_kaiming(m):
    classname = m.__class__.__name__
    if classname.find('Linear') != -1:
        nn.init.kaiming_normal_(m.weight, a=0, mode='fan_out')
        nn.init.constant_(m.bias, 0.0)
    elif classname.find('Conv') != -1:
        nn.init.kaiming_normal_(m.weight, a=0, mode='fan_in')
        if m.bias is not None:
            nn.init.constant_(m.bias, 0.0)
    elif classname.find('BatchNorm') != -1:
        if m.affine:
            nn.init.constant_(m.weight, 1.0)
            nn.init.constant_(m.bias, 0.0)


def weights_init_classifier(m):
    classname = m.__class__.__name__
    if classname.find('Linear') != -1:
        nn.init.normal_(m.weight, std=0.001)
        if m.bias:
            nn.init.constant_(m.bias, 0.0)

def my_weights_init(m):
    if isinstance(m, nn.Linear):
        nn.init.constant_(m.weight, 0.333)
        #nn.init.normal_(m.weight, mean=0.3, std=0.1)
        nn.init.constant_(m.bias, 0.0)
    if isinstance(m, nn.Conv2d):
        nn.init.constant_(m.weight, 0.333)
        nn.init.constant_(m.bias, 0.0)

class Baseline(nn.Module):
    in_planes = 2048

    def __init__(self, num_classes, last_stride, model_path, neck, neck_feat, model_name, pretrain_choice):
        super(Baseline, self).__init__()

        if model_name == 'resnet50':
            self.base = ResNet(last_stride=last_stride, block=Bottleneck, layers=[3, 4, 6, 3])

        if pretrain_choice == 'imagenet':
            self.base.load_param(model_path)
            print('Loading pretrained ImageNet model......')

        self.gap = nn.AdaptiveAvgPool2d(1)
        self.num_classes = num_classes
        self.neck = neck
        self.neck_feat = neck_feat

        if self.neck == 'no':
            self.classifier = nn.Linear(self.in_planes, self.num_classes, bias=False)
            self.classifier.apply(weights_init_classifier)
        elif self.neck == 'bnneck':
            self.bottleneck = nn.BatchNorm1d(self.in_planes)
            self.bottleneck.bias.requires_grad_(False)
            self.bottleneck.apply(weights_init_kaiming)
            self.classifier = nn.Linear(self.in_planes, self.num_classes, bias=False)
            self.classifier.apply(weights_init_classifier)


        self.attention1 = nn.Sequential(
            nn.Conv2d(256, 16, kernel_size=1,padding=0),
            nn.ReLU(inplace=True),
            nn.Conv2d(16, 256, kernel_size=1,padding=0),
            nn.Sigmoid(),)
        self.attention1.apply(weights_init_kaiming)
        self.connect1 = nn.Sequential(
            nn.Conv2d(256, 2048, kernel_size=1, padding=0),
            nn.Sigmoid(),)


        self.attention2 = nn.Sequential(
            nn.Conv2d(512, 32, kernel_size=1,padding=0),
            nn.ReLU(inplace=True),
            nn.Conv2d(32, 512, kernel_size=1,padding=0),
            nn.Sigmoid(),)
        self.attention2.apply(weights_init_kaiming)
        self.connect2 = nn.Sequential(
            nn.Conv2d(512, 2048, kernel_size=1, padding=0),
            nn.Sigmoid(),)


        self.attention3 = nn.Sequential(
            nn.Conv2d(1024, 64, kernel_size=1,padding=0),
            nn.ReLU(inplace=True),
            nn.Conv2d(64, 1024, kernel_size=1,padding=0),
            nn.Sigmoid(),)
        self.attention3.apply(weights_init_kaiming)
        self.connect3 = nn.Sequential(
            nn.Conv2d(1024, 2048, kernel_size=1, padding=0),
            nn.Sigmoid(),)


        self.attention4 = nn.Sequential(
            nn.Conv2d(2048, 128, kernel_size=1,padding=0),
            nn.ReLU(inplace=True),
            nn.Conv2d(128, 2048, kernel_size=1,padding=0),
            nn.Sigmoid(),)
        self.attention4.apply(weights_init_kaiming)


    def forward(self, x):
        B, C, H, W = x.shape
        indx = np.arange(x.shape[0]//2)*2
        bs = B//2

        x = self.base.conv1(x)
        x = self.base.bn1(x)
        x = self.base.maxpool(x)
        x = self.base.layer1(x)

        if self.training:

            '========================================'
            mask1 = self.attention1(self.gap(x))
            x = x * (1 + mask1)
            mask1 = self.connect1(mask1)
            '========================================'

            x = self.base.layer2(x)

            '========================================'
            mask2 = self.attention2(self.gap(x))
            x = x * (1 + mask2)
            mask2 = self.connect2(mask2)
            '========================================'

            x = self.base.layer3(x)

            '========================================'
            mask3 = self.attention3(self.gap(x))
            x = x * (1 + mask3)
            mask3 = self.connect3(mask3)
            '========================================'

            x = self.base.layer4(x)
            mask4 = self.attention4(self.gap(x))
            pos = x * (1 + mask4)
            neg = x * (1 - mask4)
            pos = self.gap(pos).view(B, -1)
            neg = self.gap(neg).view(B, -1)
            cls_score_pos = self.classifier(self.bottleneck(pos))
            cls_score_neg = self.classifier(self.bottleneck(neg))


            # loss_mask = -torch.mean(torch.cosine_similarity(mask3, mask2))
            loss_mask = torch.mean((mask4-mask1)*(mask4-mask1)) + \
                        torch.mean((mask4-mask2)*(mask4-mask2)) + \
                        torch.mean((mask4-mask3)*(mask4-mask3))


            return cls_score_pos, cls_score_neg, pos, neg, loss_mask


        else:
            if self.neck_feat == 'after':
                return
            else:

                mask1 = self.attention1(self.gap(x))
                x = x * (1 + mask1)

                x = self.base.layer2(x)

                mask2 = self.attention2(self.gap(x))
                x = x * (1 + mask2)

                x = self.base.layer3(x)

                mask3 = self.attention3(self.gap(x))
                x = x * (1+mask3)

                x = self.base.layer4(x)

                mask4 = self.attention4(self.gap(x))
                x = x*(1+mask4)

                pos = self.gap(x).view(B, -1)
                return pos






    def load_param(self, trained_path):
        param_dict = torch.load(trained_path)
        for i in param_dict:
            if 'classifier' in i:
                continue
            self[i].copy_(param_dict[i])
            #self.state_dict()[i].copy_(param_dict[i])
