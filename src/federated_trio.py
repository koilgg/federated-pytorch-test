import torch
import torchvision
import torchvision.transforms as transforms

import math
import time

# train three models, Federated learning
# each iteration over a subset of parameters: 1) average 2) pass back average to slaves 3) SGD step
# initialize with pre-trained models (better to use common initialization)
# loop order: loop 0: parameters/layers   {
#               loop 1 : {  averaging (part of the model)
#                loop 2: { epochs/databatches  { train; } } } }
# repeat this Nloop times


torch.manual_seed(69)
default_batch=512 # no. of batches (50000/3)/default_batch
batches_for_epoch=33#(50000/3)/default_batch
Nloop=12 # how many loops over the whole network
Nepoch=1 # how many epochs?
Nadmm=3 # how many FA iterations

# regularization
lambda1=0.0001 # L1 sweet spot 0.00031
lambda2=0.0001 # L2 sweet spot ?

load_model=False
init_model=True
save_model=True
check_results=True
# if input is biased, each 1/3 training data will have
# (slightly) different normalization. Otherwise, same normalization
biased_input=True

# split 50000 training data into three
subset1=range(0,16666)
subset2=range(16666,33333)
subset3=range(33333,50000)

if biased_input:
  # slightly different normalization for each subset
  transform1=transforms.Compose(
   [transforms.ToTensor(),
     transforms.Normalize((0.5,0.5,0.5),(0.5,0.5,0.5))])
  transform2=transforms.Compose(
   [transforms.ToTensor(),
     transforms.Normalize((0.3,0.3,0.3),(0.4,0.4,0.4))])
  transform3=transforms.Compose(
   [transforms.ToTensor(),
     transforms.Normalize((0.6,0.6,0.6),(0.5,0.5,0.5))])
else:
  # same normalization for all training data
  transform=transforms.Compose(
   [transforms.ToTensor(),
     transforms.Normalize((0.5,0.5,0.5),(0.5,0.5,0.5))])
  transform1=transform
  transform2=transform
  transform3=transform

trainset1=torchvision.datasets.CIFAR10(root='./torchdata', train=True,
    download=True, transform=transform1)
trainset2=torchvision.datasets.CIFAR10(root='./torchdata', train=True,
    download=True, transform=transform2)
trainset3=torchvision.datasets.CIFAR10(root='./torchdata', train=True,
    download=True, transform=transform3)

trainloader1 = torch.utils.data.DataLoader(trainset1, batch_size=default_batch, shuffle=False, sampler=torch.utils.data.SubsetRandomSampler(subset1),num_workers=1)
trainloader2 = torch.utils.data.DataLoader(trainset2, batch_size=default_batch, shuffle=False, sampler=torch.utils.data.SubsetRandomSampler(subset2),num_workers=1)
trainloader3 = torch.utils.data.DataLoader(trainset3, batch_size=default_batch, shuffle=False, sampler=torch.utils.data.SubsetRandomSampler(subset3),num_workers=1)

if biased_input:
  testset1=torchvision.datasets.CIFAR10(root='./torchdata', train=False,
    download=True, transform=transform1)
  testset2=torchvision.datasets.CIFAR10(root='./torchdata', train=False,
    download=True, transform=transform2)
  testset3=torchvision.datasets.CIFAR10(root='./torchdata', train=False,
    download=True, transform=transform3)
else:
  testset=torchvision.datasets.CIFAR10(root='./torchdata', train=False,
    download=True, transform=transform1)
  testset1=testset
  testset2=testset
  testset3=testset

testloader1=torch.utils.data.DataLoader(testset1, batch_size=default_batch,
    shuffle=False, num_workers=0)
testloader2=torch.utils.data.DataLoader(testset2, batch_size=default_batch,
    shuffle=False, num_workers=0)
testloader3=torch.utils.data.DataLoader(testset3, batch_size=default_batch,
    shuffle=False, num_workers=0)

import numpy as np

# define a cnn
from simple_models import *

net1=Net()
net2=Net()
net3=Net()

# update from saved models
if load_model:
  checkpoint=torch.load('./s1.model')
  net1.load_state_dict(checkpoint['model_state_dict'])
  net1.train()
  checkpoint=torch.load('./s2.model')
  net2.load_state_dict(checkpoint['model_state_dict'])
  net2.train()
  checkpoint=torch.load('./s3.model')
  net3.load_state_dict(checkpoint['model_state_dict'])
  net3.train()

########################################################################### helper functions
def init_weights(m):
  if type(m)==nn.Linear or type(m)==nn.Conv2d:
    torch.nn.init.xavier_uniform_(m.weight)
    m.bias.data.fill_(0.01)

def unfreeze_one_layer(net,layer_id):
  ' set all layers to not-trainable except the layer given by layer_id (0,1,..)'
  for ci,param in enumerate(net.parameters(),0):
    if (ci == 2*layer_id) or (ci==2*layer_id+1):
       param.requires_grad=True
    else:
       param.requires_grad=False

def unfreeze_all_layers(net):
  ' reset all layers to trainable'
  for ci,param in enumerate(net.parameters(),0):
    param.requires_grad=True

def get_trainable_values(net):
  ' return trainable parameter values as a vector (only the first parameter set)'
  trainable=filter(lambda p: p.requires_grad, net.parameters())
  paramlist=list(trainable) 
  N=0
  for params in paramlist:
    N+=params.numel()
  X=torch.empty(N,dtype=torch.float)
  X.fill_(0.0)
  offset=0
  for params in paramlist:
    numel=params.numel()
    with torch.no_grad():
      X[offset:offset+numel].copy_(params.data.view_as(X[offset:offset+numel].data))
    offset+=numel

  return X


def put_trainable_values(net,X):
  ' replace trainable parameter values by the given vector (only the first parameter set)'
  trainable=filter(lambda p: p.requires_grad, net.parameters())
  paramlist=list(trainable)
  offset=0
  for params in paramlist:
    numel=params.numel()
    with torch.no_grad():
     params.data.copy_(X[offset:offset+numel].data.view_as(params.data))
    offset+=numel


def number_of_layers(net):
  ' get total number of layers (note: each layers has weight and bias , so count as 2) '
  for ci,param in enumerate(net.parameters(),0):
   pass
  return int((ci+1)/2) # because weight+bias belong to one layer

def distance_of_layers(net1,net2,net3):
  'find Eculidean distance of each layer (from the mean) and return this as vector (normalized by size of layer)'
  L=number_of_layers(net1)
  W=np.zeros(L)
  for ci in range(L):
   unfreeze_one_layer(net1,ci)
   unfreeze_one_layer(net2,ci)
   unfreeze_one_layer(net3,ci)
   W1=get_trainable_values(net1)
   W2=get_trainable_values(net2)
   W3=get_trainable_values(net3)
   N=W1.numel()
   Wm=(W1+W2+W3)/3
   W[ci]=(Wm-W1).norm()/N
   W[ci]+=(Wm-W2).norm()/N
   W[ci]+=(Wm-W3).norm()/N
  return W

def sthreshold(z,sval):
  """soft threshold a tensor  
    if element(z) > sval, element(z)=sval
    if element(z) < -sval, element(z)=-sval 
  """
  with torch.no_grad():
    T=nn.Softshrink(sval) # if z_i < -sval, z_i -> z_i +sval , ...
    z=T(z)
  return z


def verification_error_check(net1,net2,net3):
  correct1=0
  correct2=0
  correct3=0
  total=0

  for data in testloader1:
    images,labels=data
    outputs=net1(Variable(images))
    _,predicted=torch.max(outputs.data,1)
    correct1 += (predicted==labels).sum()
    total += labels.size(0)
  for data in testloader2:
    images,labels=data
    outputs=net2(Variable(images))
    _,predicted=torch.max(outputs.data,1)
    correct2 += (predicted==labels).sum()
  for data in testloader3:
    images,labels=data
    outputs=net3(Variable(images))
    _,predicted=torch.max(outputs.data,1)
    correct3 += (predicted==labels).sum()

  print('Accuracy of the network on the %d test images:%%%f %%%f %%%f'%
     (total,100*correct1/total,100*correct2/total,100*correct3/total))



##############################################################################################

if init_model:
  # note: use same seed for random number generation
  torch.manual_seed(0)
  net1.apply(init_weights)
  torch.manual_seed(0)
  net2.apply(init_weights)
  torch.manual_seed(0)
  net3.apply(init_weights)


criterion1=nn.CrossEntropyLoss()
criterion2=nn.CrossEntropyLoss()
criterion3=nn.CrossEntropyLoss()

L=number_of_layers(net1)
# get layer ids in given order 0..L-1 for selective training
np.random.seed(0)# get same list
Li=net1.train_order_layer_ids()
# make sure number of layers match
if L != len(Li):
  print("Warning, expected number of layers and given layer ids do not agree")
else:
  print(Li)

from lbfgsnew import LBFGSNew # custom optimizer
import torch.optim as optim
############### loop 00 (over the full net)
for nloop in range(Nloop):
  ############ loop 0 (over layers of the network)
  for ci in Li:
   unfreeze_one_layer(net1,ci)
   unfreeze_one_layer(net2,ci)
   unfreeze_one_layer(net3,ci)
   trainable=filter(lambda p: p.requires_grad, net1.parameters())
   params_vec1=torch.cat([x.view(-1) for x in list(trainable)])
  
   # number of parameters trained
   N=params_vec1.numel()
   z=torch.empty(N,dtype=torch.float,requires_grad=False)
   z.fill_(0.0)
  
   #opt1=optim.Adam(filter(lambda p: p.requires_grad, net1.parameters()),lr=0.001)
   #opt2=optim.Adam(filter(lambda p: p.requires_grad, net2.parameters()),lr=0.001)
   #opt3=optim.Adam(filter(lambda p: p.requires_grad, net3.parameters()),lr=0.001)
   opt1 =LBFGSNew(filter(lambda p: p.requires_grad, net1.parameters()), history_size=10, max_iter=4, line_search_fn=True,batch_mode=True)
   opt2 =LBFGSNew(filter(lambda p: p.requires_grad, net2.parameters()), history_size=10, max_iter=4, line_search_fn=True,batch_mode=True)
   opt3 =LBFGSNew(filter(lambda p: p.requires_grad, net3.parameters()), history_size=10, max_iter=4, line_search_fn=True,batch_mode=True)
  
   ############# loop 1 (Federated avaraging for subset of model)
   for nadmm in range(Nadmm):
     ##### loop 2 (data)
     for epoch in range(Nepoch):
        running_loss1=0.0
        running_loss2=0.0
        running_loss3=0.0
  
        for i,(data1,data2,data3) in enumerate(zip(trainloader1,trainloader2,trainloader3),0):
           # get the inputs
           inputs1,labels1=data1
           inputs2,labels2=data2
           inputs3,labels3=data3
           # wrap them in variable
           inputs1,labels1=Variable(inputs1),Variable(labels1)
           inputs2,labels2=Variable(inputs2),Variable(labels2)
           inputs3,labels3=Variable(inputs3),Variable(labels3)
    
  
           trainable=filter(lambda p: p.requires_grad, net1.parameters())
           params_vec1=torch.cat([x.view(-1) for x in list(trainable)])
           trainable=filter(lambda p: p.requires_grad, net2.parameters())
           params_vec2=torch.cat([x.view(-1) for x in list(trainable)])
           trainable=filter(lambda p: p.requires_grad, net3.parameters())
           params_vec3=torch.cat([x.view(-1) for x in list(trainable)])
  
           # fc1 and fc3 layers have L1 and L2 regularization
           def closure1():
                 if torch.is_grad_enabled():
                    opt1.zero_grad()
                 outputs=net1(inputs1)
                 loss=criterion1(outputs,labels1)
                 if ci in net1.linear_layer_ids():
                    loss+=lambda1*torch.norm(params_vec1,1)+lambda2*(torch.norm(params_vec1,2)**2)
                 if loss.requires_grad:
                    loss.backward()
                 return loss
           def closure2():
                 if torch.is_grad_enabled():
                    opt2.zero_grad()
                 outputs=net2(inputs2)
                 loss=criterion2(outputs,labels2)
                 if ci in net2.linear_layer_ids():
                    loss+=lambda1*torch.norm(params_vec2,1)+lambda2*(torch.norm(params_vec2,2)**2)
                 if loss.requires_grad:
                    loss.backward()
                 return loss
           def closure3():
                 if torch.is_grad_enabled():
                    opt3.zero_grad()
                 outputs=net3(inputs3)
                 loss=criterion3(outputs,labels3)
                 if ci in net3.linear_layer_ids():
                    loss+=lambda1*torch.norm(params_vec3,1)+lambda2*(torch.norm(params_vec3,2)**2)
                 if loss.requires_grad:
                    loss.backward()
                 return loss
  
           # ADMM step 1
           opt1.step(closure1)
           opt2.step(closure2)
           opt3.step(closure3)
  
           # only for diagnostics
           outputs1=net1(inputs1)
           loss1=criterion1(outputs1,labels1).data.item()
           running_loss1 +=loss1
           outputs2=net2(inputs2)
           loss2=criterion2(outputs2,labels2).data.item()
           running_loss2 +=loss2
           outputs3=net3(inputs3)
           loss3=criterion3(outputs3,labels3).data.item()
           running_loss3 +=loss3
  
           
           print('layer=%d %d(%d) minibatch=%d epoch=%d losses %e,%e,%e'%(ci,nloop,N,i,epoch,loss1,loss2,loss3))
     # Federated averaging
     x1=get_trainable_values(net1)
     x2=get_trainable_values(net2)
     x3=get_trainable_values(net3)
     znew=(x1+x2+x3)/3
     dual_residual=torch.norm(z-znew).item()/N # per parameter
     print('dual (loop=%d,layer=%d,avg=%d)=%e'%(nloop,ci,nadmm,dual_residual))
     z=znew
     put_trainable_values(net1,z)
     put_trainable_values(net2,z)
     put_trainable_values(net3,z)

     if check_results:
       verification_error_check(net1,net2,net3)
  

print('Finished Training')


if save_model:
 torch.save({
     'model_state_dict':net1.state_dict(),
     'epoch':epoch,
     'optimizer_state_dict':opt1.state_dict(),
     'running_loss':running_loss1,
     },'./s1.model')
 torch.save({
     'model_state_dict':net2.state_dict(),
     'epoch':epoch,
     'optimizer_state_dict':opt2.state_dict(),
     'running_loss':running_loss2,
     },'./s2.model')
 torch.save({
     'model_state_dict':net3.state_dict(),
     'epoch':epoch,
     'optimizer_state_dict':opt3.state_dict(),
     'running_loss':running_loss3,
     },'./s3.model')
