import tensorboardX
import torch
import torch.nn as nn
import torchvision.datasets as vdset
import torchvision.transforms as vtransforms
from torch.autograd import Variable

from gans import arguments, models, scores, utils

if __name__ == '__main__':

    opt = arguments.get_arguments()

    writer = tensorboardX.SummaryWriter(opt.outf)

    # DATA
    print(f'Loading dataset {opt.dataset} at {opt.dataroot}')
    # folder dataset
    dataset = vdset.ImageFolder(
        root=opt.dataroot,
        transform=vtransforms.Compose([
            vtransforms.Resize(opt.imageSize),
            vtransforms.CenterCrop(opt.imageSize),
            vtransforms.ToTensor(),
            vtransforms.Normalize(
                (0.5, 0.5, 0.5),
                (0.5, 0.5, 0.5)
            ),
        ]))
    dataloader = torch.utils.data.DataLoader(
        dataset, batch_size=opt.batchSize,
        shuffle=True, num_workers=int(opt.workers)
    )
    print('Dataloader done')

    # INITIALIZE MODELS
    netG = models.GeneratorNet(opt)
    netD = models.DiscriminatorNet(opt)

    # Reload past models for a warm start
    if opt.netD != '':
        netD.load_state_dict(torch.load(opt.netD))
    if opt.netG != '':
        netG.load_state_dict(torch.load(opt.netG))

    print(netD)
    print(netG)

    criterion = nn.BCEWithLogitsLoss()
    if opt.mode == 'lsgan':
        criterion = nn.MSELoss()
    if opt.mode == 'wgan':
        def criterion(out, target):
            return ((1 - 2 * target) * out).mean()

    sigmoid = nn.Sigmoid()

    # input to the discriminator of size [opt.batchSize, 3, opt.imageSize, opt.imageSize]
    # input noise for the generator
    noise = torch.FloatTensor(opt.batchSize, opt.nz, 1, 1)
    # input noise to plot samples
    fixed_noise = torch.FloatTensor(opt.batchSize, opt.nz, 1, 1).normal_(0, 1)

    # labels to use with criterion
    label = torch.FloatTensor(opt.batchSize)
    real_label = 1
    fake_label = 0

    if opt.cuda:
        netD.cuda()
        netG.cuda()
        label = label.cuda()
        noise, fixed_noise = noise.cuda(), fixed_noise.cuda()

    fixed_noise = Variable(fixed_noise)

    # setup optimizer
    optimizerD = torch.optim.Adam(
        netD.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))
    optimizerG = torch.optim.Adam(
        netG.parameters(), lr=opt.lr, betas=(opt.beta1, 0.999))

    step = 0
    for epoch in range(opt.niter):
        for i, data in enumerate(dataloader):
            step += 1
            for _ in range(opt.critic_iter):
                ############################
                # (1) Update D network: maximize log(D(x)) + log(1 - D(G(z)))
                ###########################
                # train with real
                netD.zero_grad()

                real_cpu, _ = data
                batch_size = real_cpu.size(0)

                real_samples = real_cpu.cuda() if opt.cuda else real_cpu.clone()
                real = Variable(real_samples)
                label.resize_(batch_size).fill_(real_label)
                labelv = Variable(label)

                real_out = netD(real).squeeze()
                errD_real = criterion(real_out, labelv)
                errD_real.backward()

                # train with fake
                noise.resize_(batch_size, opt.nz, 1, 1).normal_(0, 1)
                noisev = Variable(noise)
                labelv = Variable(label.fill_(fake_label))

                fake = netG(noisev)
                fake_out = netD(fake.detach()).squeeze()
                errD_fake = criterion(fake_out, labelv)
                errD_fake.backward()

                # gradient penalty
                if opt.lanbda > 0:
                    if opt.penalty == 'real':
                        inp = real.data
                    elif opt.penalty == 'fake':
                        inp = fake.data
                    elif opt.penalty == 'both':
                        inp = torch.cat([real.data, fake.data], dim=0)
                    elif opt.penalty == 'uniform':
                        inp = 256 * torch.rand(real.size())
                    gp = netD.gradient_penalty(inp)
                    (opt.lanbda * gp).backward()
                else:
                    gp = 0

                # update
                optimizerD.step()

                # monitor
                real_acc = sigmoid(real_out).data.round().mean()
                f_x = real_out.data.mean()
                D_x = sigmoid(real_out).data.mean()

                fake_acc = 1 - sigmoid(fake_out).data.round().mean()
                f_G_z1 = fake_out.data.mean()
                D_G_z1 = sigmoid(fake_out).data.mean()
                errD = errD_real + errD_fake

            for k in range(opt.gen_iter):
                ############################
                # (2) Update G network: maximize log(D(G(z)))
                ###########################
                if k > 0:
                    noise.resize_(opt.batchSize, opt.nz, 1, 1).normal_(0, 1)
                    noisev = Variable(noise)
                    fake = netG(noisev)

                netG.zero_grad()

                fake_out = netD(fake).squeeze()
                if opt.mode == 'nsgan':  # non-saturating gan
                    # use the real labels (1) for generator cost
                    labelv = Variable(label.fill_(real_label))
                    errG = criterion(fake_out, labelv)
                elif opt.mode == 'mmgan':  # minimax gan
                    # use fake labels and opposite of criterion
                    labelv = Variable(label.fill_(fake_label))
                    errG = - criterion(fake_out, labelv)
                elif opt.mode == 'lsgan':  # least square gan NOT WORKING
                    # use real labels for generator
                    labelv = Variable(label.fill_(real_label))
                    errG = criterion(fake_out, labelv)
                elif opt.mode == 'wgan':
                    labelv = Variable(label.fill_(real_label))
                    errG = criterion(fake_out, labelv)

                errG.backward()
                optimizerG.step()

                # monitor
                f_G_z2 = fake_out.data.mean()
                D_G_z2 = sigmoid(fake_out).data.mean()

            if step % 50 == 0:  # print and log info
                print('[%d/%d][%d/%d] Loss_D: %.4f Loss_G: %.4f D(x): %.4f D(G(z)): %.4f / %.4f'
                      % (epoch, opt.niter, i, len(dataloader),
                         errD.data[0], errG.data[0], D_x, D_G_z1, D_G_z2))
                info = {
                    'disc_cost': errD.data[0],
                    'gen_cost': errG.data[0],
                    'f_x': f_x,
                    'f_G_z1': f_G_z1,
                    'D_x': D_x,
                    'D_G_z': D_G_z1,
                    'acc_real': real_acc,
                    'acc_fake': fake_acc,
                    'logit_dist': f_x - f_G_z1,
                    'penalty': opt.lanbda * gp.data[0]
                }

                for tag, val in info.items():
                    writer.add_scalar(tag, val, global_step=step)

            if step % 100 == 0:  # plot samples
                utils.plot_images(
                    opt.outf, writer, 'real_samples', real_samples, step)
                fake = netG(fixed_noise)
                utils.plot_images(
                    opt.outf, writer, 'fake_samples', fake.data, step)
                interpolation_noise, interpolated_noise = \
                    utils.make_interpolation_noise(opt.nz)

                # interpolation in the latent space
                interpolation_noise = Variable(interpolation_noise).cuda()
                fake_interpolation = netG(interpolation_noise)
                utils.plot_images(
                    opt.outf, writer, 'fake_interpolation_samples',
                    fake_interpolation.data, step, nrow=10)

                # interpolation in the sample space
                interpolated_noise = Variable(interpolated_noise).cuda()
                fake_interpolated = netG(interpolated_noise)
                x_interpolation = \
                    utils.make_interpolation_samples(fake_interpolated.data)
                utils.plot_images(
                    opt.outf, writer, 'fake_x_interpolation',
                    x_interpolation, step, nrow=10)

                ## Save parameters histogram
                # for name, param in netD.named_parameters():
                #     writer.add_histogram(name, param.clone().cpu().data.numpy(), step)
                # for name, param in netG.named_parameters():
                #     writer.add_histogram(name, param.clone().cpu().data.numpy(), step)

            if step % 500 == 0:  # compute scores
                incep_score, _ = scores.inception_score(fake.data, resize=True)
                md_score, _ = scores.mode_score(fake.data, real_samples, resize=True)
                print(f'Inception: {incep_score} and Mode score: {md_score}\n')
                writer.add_scalar('inception_score', incep_score, global_step=step)
                writer.add_scalar('mode_score', md_score, global_step=step)

        # do checkpointing
        if epoch % 5 == 0:
            torch.save(netG.state_dict(), '%s/netG_epoch_%d.pth' % (opt.outf, epoch))
            torch.save(netD.state_dict(), '%s/netD_epoch_%d.pth' % (opt.outf, epoch))
