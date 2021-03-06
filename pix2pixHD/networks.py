import torch
import torch.nn as nn
from utils import get_grid, get_norm_layer, get_pad_layer
from math import log2


class Generator(nn.Module):
    def __init__(self, opt):
        super(Generator, self).__init__()
        if opt.HD:
            act = nn.ReLU(inplace=True)
            input_ch = opt.input_ch
            n_gf = opt.n_gf
            norm = get_norm_layer(opt.norm_type)
            output_ch = opt.output_ch
            pad = get_pad_layer(opt.padding_type)

            model = []
            model += [pad(3), nn.Conv2d(input_ch, n_gf, kernel_size=7, padding=0), norm(n_gf), act]

            for _ in range(opt.n_downsample):
                model += [nn.Conv2d(n_gf, 2 * n_gf, kernel_size=3, padding=1, stride=2), norm(2 * n_gf), act]
                n_gf *= 2

            for _ in range(opt.n_residual):
                model += [ResidualBlock(n_gf, pad, norm, act)]

            for _ in range(opt.n_downsample):
                model += [nn.ConvTranspose2d(n_gf, n_gf//2, kernel_size=3, padding=1, stride=2, output_padding=1),
                          norm(n_gf//2), act]
                n_gf //= 2

            model += [pad(3), nn.Conv2d(n_gf, output_ch, kernel_size=7, padding=0), nn.Tanh()]
            self.model = nn.Sequential(*model)

        else:
            act_down = nn.LeakyReLU(0.2, inplace=True)
            act_up = nn.ReLU(inplace=True)
            image_height = opt.image_height
            input_ch = opt.input_ch
            max_ch = opt.max_ch
            n_downsample = int(log2(image_height))
            n_gf = opt.n_gf
            norm = nn.BatchNorm2d
            output_ch = opt.output_ch

            idx_max_ch = int(log2(max_ch // n_gf))
            for i in range(n_downsample):
                if i == 0:
                    down_block = [nn.Conv2d(input_ch, n_gf, kernel_size=4, padding=1, stride=2, bias=False)]
                    up_block = [act_up,
                                nn.ConvTranspose2d(2 * n_gf, output_ch, kernel_size=4, padding=1, stride=2, bias=False),
                                nn.Tanh()]

                elif 1 <= i <= idx_max_ch:
                    down_block = [act_down,
                                  nn.Conv2d(n_gf, 2 * n_gf, kernel_size=4, padding=1, stride=2, bias=False),
                                  norm(2 * n_gf)]

                    up_block = [act_up,
                                nn.ConvTranspose2d(4 * n_gf, n_gf, kernel_size=4, padding=1, stride=2, bias=False),
                                norm(n_gf)]

                elif idx_max_ch < i < n_downsample - 4:
                    down_block = [act_down,
                                  nn.Conv2d(n_gf, n_gf, kernel_size=4, padding=1, stride=2, bias=False),
                                  norm(n_gf)]

                    up_block = [act_up,
                                nn.ConvTranspose2d(2 * n_gf, n_gf, kernel_size=4, padding=1, stride=2, bias=False),
                                norm(n_gf)]

                elif n_downsample - 4 <= i < n_downsample - 1:
                    down_block = [act_down,
                                  nn.Conv2d(n_gf, n_gf, kernel_size=4, padding=1, stride=2, bias=False),
                                  norm(n_gf)]

                    up_block = [act_up,
                                nn.ConvTranspose2d(2 * n_gf, n_gf, kernel_size=4, padding=1, stride=2, bias=False),
                                norm(n_gf), nn.Dropout2d(0.5, inplace=True)]

                else:
                    down_block = [act_down,
                                  nn.Conv2d(n_gf, n_gf, kernel_size=4, padding=1, stride=2, bias=False)]

                    up_block = [act_up,
                                nn.ConvTranspose2d(n_gf, n_gf, kernel_size=4, padding=1, stride=2, bias=False),
                                norm(n_gf),
                                nn.Dropout2d(0.5, inplace=True)]

                self.add_module('Down_block_{}'.format(i), nn.Sequential(*down_block))
                self.add_module('Up_block_{}'.format(i), nn.Sequential(*up_block))
                n_gf *= 2 if n_gf < max_ch and i != 0 else 1

            self.n_downsample = n_downsample
        self.HD = opt.HD
        print(self)
        print("the number of G parameters", sum(p.numel() for p in self.parameters() if p.requires_grad))

    def forward(self, x):
        if self.HD:
            return self.model(x)

        else:
            layers = [x]
            for i in range(self.n_downsample):
                layers += [getattr(self, 'Down_block_{}'.format(i))(layers[-1])]

            x = getattr(self, 'Up_block_{}'.format(self.n_downsample - 1))(layers[-1])
            for i in range(self.n_downsample - 1, 0, -1):
                x = getattr(self, 'Up_block_{}'.format(i - 1))(torch.cat([x, layers[i]], dim=1))
            return x


class PatchDiscriminator(nn.Module):
    def __init__(self, opt):
        super(PatchDiscriminator, self).__init__()

        act = nn.LeakyReLU(0.2, inplace=True)
        input_channel = opt.input_ch + opt.output_ch
        n_df = opt.n_df
        norm = nn.InstanceNorm2d if opt.HD else nn.BatchNorm2d

        blocks = []
        blocks += [[nn.Conv2d(input_channel, n_df, kernel_size=4, padding=1, stride=2), act]]
        blocks += [[nn.Conv2d(n_df, 2 * n_df, kernel_size=4, padding=1, stride=2), norm(2 * n_df), act]]
        blocks += [[nn.Conv2d(2 * n_df, 4 * n_df, kernel_size=4, padding=1, stride=2), norm(4 * n_df), act]]
        blocks += [[nn.Conv2d(4 * n_df, 8 * n_df, kernel_size=4, padding=1, stride=1), norm(8 * n_df), act]]

        if opt.HD:
            blocks += [[nn.Conv2d(8 * n_df, 1, kernel_size=4, padding=1, stride=1)]]

        else:
            blocks += [[nn.Conv2d(8 * n_df, 1, kernel_size=4, padding=1, stride=1), nn.Sigmoid()]]

        self.n_blocks = len(blocks)
        for i in range(self.n_blocks):
            setattr(self, 'block_{}'.format(i), nn.Sequential(*blocks[i]))

    def forward(self, x):
        result = [x]
        for i in range(self.n_blocks):
            block = getattr(self, 'block_{}'.format(i))
            result.append(block(result[-1]))

        return result[1:]  # except for the input


class Discriminator(nn.Module):
    def __init__(self, opt):
        super(Discriminator, self).__init__()

        if opt.HD:
            for i in range(opt.n_D):
                setattr(self, 'Scale_{}'.format(str(i)), PatchDiscriminator(opt))
            self.n_D = 2

        else:
            self.Scale_0 = PatchDiscriminator(opt)
            self.n_D = 1

        print(self)
        print("the number of D parameters", sum(p.numel() for p in self.parameters() if p.requires_grad))

    def forward(self, x):
        result = []
        for i in range(self.n_D):
            result.append(getattr(self, 'Scale_{}'.format(i))(x))
            if i != self.n_D - 1:
                x = nn.AvgPool2d(kernel_size=3, padding=1, stride=2, count_include_pad=False)(x)
        return result


class Loss(object):
    def __init__(self, opt):
        self.opt = opt
        self.device = torch.device('cuda:0' if opt.gpu_ids != -1 else 'cpu:0')
        self.dtype = torch.float16 if opt.data_type == 16 else torch.float32

        if opt.HD:
            self.criterion = nn.MSELoss()
            self.FMcriterion = nn.L1Loss()
            self.n_D = 2

        else:
            self.criterion = nn.BCELoss()
            self.n_D = 1

    def __call__(self, D, G, input, target):
        loss_D = 0
        loss_G = 0
        loss_G_FM = 0

        fake = G(input)

        real_features = D(torch.cat((input, target), dim=1))
        fake_features = D(torch.cat((input, fake.detach()), dim=1))

        for i in range(self.n_D):
            real_grid = get_grid(real_features[i][-1], is_real=True).to(self.device, self.dtype)
            fake_grid = get_grid(fake_features[i][-1], is_real=False).to(self.device, self.dtype)
            # it doesn't need to be fake_features

            loss_D += (self.criterion(real_features[i][-1], real_grid) +
                       self.criterion(fake_features[i][-1], fake_grid)) * 0.5

        fake_features = D(torch.cat((input, fake), dim=1))

        for i in range(self.n_D):
            real_grid = get_grid(fake_features[i][-1], is_real=True).to(self.device, self.dtype)
            loss_G += self.criterion(fake_features[i][-1], real_grid)

            if self.opt.HD:
                for j in range(len(fake_features[0])):
                    loss_G_FM += self.FMcriterion(fake_features[i][j], real_features[i][j].detach())
                loss_G += loss_G_FM * (1.0 / self.opt.n_D) * self.opt.lambda_FM

        return loss_D, loss_G, target, fake


class ResidualBlock(nn.Module):
    def __init__(self, n_channels, pad, norm, act):
        super(ResidualBlock, self).__init__()
        block = [pad(1), nn.Conv2d(n_channels, n_channels, kernel_size=3, padding=0, stride=1), norm(n_channels), act]
        block += [pad(1), nn.Conv2d(n_channels, n_channels, kernel_size=3, padding=0, stride=1), norm(n_channels)]
        self.block = nn.Sequential(*block)

    def forward(self, x):
        return x + self.block(x)
