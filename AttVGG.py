import torch.nn as nn
import torch
import torch.nn.functional as F
from torch.optim import optimizer

from torchvision import models

class AttentionBlock(nn.Module):
    def __init__(self, in_features_l, in_features_g, attn_features, up_factor, normalize_attn=True):
        super(AttentionBlock, self).__init__()
        self.up_factor = up_factor
        self.normalize_attn = normalize_attn
        self.W_l = nn.Conv2d(in_channels=in_features_l, out_channels=attn_features, kernel_size=1, padding=0,
                             bias=False)
        self.W_g = nn.Conv2d(in_channels=in_features_g, out_channels=attn_features, kernel_size=1, padding=0,
                             bias=False)
        self.phi = nn.Conv2d(in_channels=attn_features, out_channels=1, kernel_size=1, padding=0, bias=True)

    def forward(self, l, g):
        N, C, W, H = l.size()
        l_ = self.W_l(l)
        g_ = self.W_g(g)
        #print(l.shape, g.shape)
        #print(l_.shape, g_.shape)
        if self.up_factor > 1:
            #g_ = F.interpolate(g_, scale_factor=self.up_factor, mode='bilinear', align_corners=False)
            g_ = F.interpolate(g_, size=(W, H), mode='bilinear', align_corners=False)

        #print("after interpolate",l_.shape, g_.shape)
        c = self.phi(F.relu(l_ + g_))  # batch_sizex1xWxH

        # compute attn map
        if self.normalize_attn:
            a = F.softmax(c.view(N, 1, -1), dim=2).view(N, 1, W, H)
        else:
            a = torch.sigmoid(c)
        # re-weight the local feature
        f = torch.mul(a.expand_as(l), l)  # batch_sizexCxWxH
        if self.normalize_attn:
            output = f.view(N, C, -1).sum(dim=2)  # weighted sum
        else:
            output = F.adaptive_avg_pool2d(f, (1, 1)).view(N, C)  # global average pooling
        return a, output


class AttnVGG(nn.Module):
    def __init__(self, num_classes = 6, normalize_attn=False, dropout=None):
        super(AttnVGG, self).__init__()
        net = models.vgg16_bn(pretrained=True)
        self.conv_block1 = nn.Sequential(*list(net.features.children())[0:6])
        self.conv_block2 = nn.Sequential(*list(net.features.children())[7:13])
        self.conv_block3 = nn.Sequential(*list(net.features.children())[14:23])
        self.conv_block4 = nn.Sequential(*list(net.features.children())[24:33])
        self.conv_block5 = nn.Sequential(*list(net.features.children())[34:43])
        self.pool = nn.AvgPool2d((7, 3), stride=1)
        self.dpt = None
        if dropout is not None:
            self.dpt = nn.Dropout(dropout)

        self.cls = nn.Linear(in_features=512 + 512 + 256, out_features=num_classes, bias=True)
        

        #self.cls = nn.Sigmoid()
        # initialize the attention blocks defined above
        self.attn1 = AttentionBlock(256, 512, 256, 4, normalize_attn=normalize_attn)
        self.attn2 = AttentionBlock(512, 512, 256, 2, normalize_attn=normalize_attn)


        #self.reset_parameters(self.cls)
        self.reset_parameters(self.attn1)
        self.reset_parameters(self.attn2)

    def reset_parameters(self, module):
        for m in module.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_in', nonlinearity='relu')
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0.)
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.constant_(m.weight, 1.)
                nn.init.constant_(m.bias, 0.)
            elif isinstance(m, nn.Linear):
                nn.init.normal_(m.weight, 0., 0.01)
                nn.init.constant_(m.bias, 0.)

    def forward(self, x):
        block1 = self.conv_block1(x)  # /1
        pool1 = F.max_pool2d(block1, 2, 2)  # /2
        block2 = self.conv_block2(pool1)  # /2
        pool2 = F.max_pool2d(block2, 2, 2)  # /4
        block3 = self.conv_block3(pool2)  # /4
        pool3 = F.max_pool2d(block3, 2, 2)  # /8
        block4 = self.conv_block4(pool3)  # /8
        pool4 = F.max_pool2d(block4, 2, 2)  # /16
        block5 = self.conv_block5(pool4)  # /16
        pool5 = F.max_pool2d(block5, 2, 2)  # /32
        N, __, __, __ = pool5.size()
        #print(pool5.size())

        #print(pool5.shape)
        g = self.pool(pool5).view(N, 512)
        a1, g1 = self.attn1(pool3, pool5)
        a2, g2 = self.attn2(pool4, pool5)
        g_hat = torch.cat((g, g1, g2), dim=1)  # batch_size x C
        if self.dpt is not None:
            g_hat = self.dpt(g_hat)
        out = self.cls(g_hat)
        
        res = torch.sigmoid(out)

        return [res, a1, a2]