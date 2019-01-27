import torch
import torch.nn as nn


def weights_init(m):
    if isinstance(m, nn.Conv2d) or isinstance(m, nn.Linear):
        nn.init.xavier_uniform(m.weight.data)
        nn.init.constant(m.bias, 0.1)


class _Net(nn.Module):
    def __init__(self, cls_factor=1, box_factor=1, landmark_factor=1, is_train=False, device='cpu'):
        super(_Net, self).__init__()

        self.is_train = is_train
        self.device = device

        self._init_net()

        if is_train:
            # loss function
            self.cls_factor = cls_factor
            self.box_factor = box_factor
            self.land_factor = landmark_factor
            self.loss_cls = nn.BCELoss()
            self.loss_box = nn.MSELoss()
            self.loss_landmark = nn.MSELoss()

        # weight initiation with xavier
        self.apply(weights_init)

        # Move tensor to target device
        self.to(device)

        if not self.is_train:
            self.eval()

    def get_loss(self, x, gt_label, gt_boxes, gt_landmarks):
        """
        Get total loss.
        Arguments:
            x {Tensor} -- Input normalized images. (Note here: rnet, onet only support fix size images.)
            gt_label {Tensor} -- Ground truth label.
            gt_boxes {Tensor} -- Ground truth boxes coordinate.
        
        Returns:
            Tensor -- classification loss + box regression loss
        """
        if not self.is_train:
            raise AssertionError("Method 'get_loss' is avaliable only when 'is_train' is True.")

        # Forward pass
        pred_label, pred_offset, pred_landmarks = self.forward(x)

        # Reshape the tensor
        pred_label = pred_label.view(-1, 1)
        pred_offset = pred_offset.view(-1, 4)
        pred_landmarks = pred_landmarks.view(-1, 10)

        # Compute the loss
        cls_loss = self.cls_loss(gt_label, pred_label)
        box_loss = self.box_loss(gt_label, gt_boxes, pred_offset)

        return cls_loss * self.cls_factor + box_loss * self.box_factor

    def _init_net(self):
        raise NotImplementedError
    
    def cls_loss(self,gt_label,pred_label):
        pred_label = torch.squeeze(pred_label)
        gt_label = torch.squeeze(gt_label)
        # get the mask element which >= 0, only 0 and 1 can effect the detection loss
        mask = torch.ge(gt_label,0)
        valid_gt_label = torch.masked_select(gt_label,mask)
        valid_pred_label = torch.masked_select(pred_label,mask)
        return self.loss_cls(valid_pred_label,valid_gt_label)*self.cls_factor


    def box_loss(self,gt_label,gt_offset,pred_offset):
        pred_offset = torch.squeeze(pred_offset)
        gt_offset = torch.squeeze(gt_offset)
        gt_label = torch.squeeze(gt_label)

        #get the mask element which != 0
        unmask = torch.eq(gt_label,0)
        mask = torch.eq(unmask,0)
        #convert mask to dim index
        chose_index = torch.nonzero(mask.data)
        chose_index = torch.squeeze(chose_index)
        #only valid element can effect the loss
        valid_gt_offset = gt_offset[chose_index,:]
        valid_pred_offset = pred_offset[chose_index,:]
        return self.loss_box(valid_pred_offset,valid_gt_offset)*self.box_factor


    def landmark_loss(self,gt_label,gt_landmark,pred_landmark):
        pred_landmark = torch.squeeze(pred_landmark)
        gt_landmark = torch.squeeze(gt_landmark)
        gt_label = torch.squeeze(gt_label)
        mask = torch.eq(gt_label,-2)

        chose_index = torch.nonzero(mask.data)
        chose_index = torch.squeeze(chose_index)

        valid_gt_landmark = gt_landmark[chose_index, :]
        valid_pred_landmark = pred_landmark[chose_index, :]
        return self.loss_landmark(valid_pred_landmark,valid_gt_landmark)*self.land_factor


class PNet(_Net):

    def __init__(self, **kwargs):
        # Hyper-parameter from original papaer
        param = [1, 0.5, 0.5]
        super(PNet, self).__init__(*param, **kwargs)

    def _init_net(self):

        # backend
        self.body = nn.Sequential(
            nn.Conv2d(3, 10, kernel_size=3, stride=1),  # conv1
            nn.PReLU(),  # PReLU1
            nn.MaxPool2d(kernel_size=2, stride=2),  # pool1
            nn.Conv2d(10, 16, kernel_size=3, stride=1),  # conv2
            nn.PReLU(),  # PReLU2
            nn.Conv2d(16, 32, kernel_size=3, stride=1),  # conv3
            nn.PReLU()  # PReLU3
        )

        # detection
        self.cls = nn.Sequential(
            nn.Conv2d(32, 1, kernel_size=1, stride=1),
            nn.Sigmoid()
        )
        # bounding box regresion
        self.box_offset = nn.Conv2d(32, 4, kernel_size=1, stride=1)
        # landmark regression
        self.landmarks = nn.Conv2d(32, 10, kernel_size=1, stride=1)


    def forward(self, x):
        feature_map = self.body(x)
        label = self.cls(feature_map)
        offset = self.box_offset(feature_map)
        landmarks = self.landmarks(feature_map)

        return label, offset, landmarks


class RNet(_Net):

    def __init__(self, **kwargs):
        # Hyper-parameter from original papaer
        param = [1, 0.5, 0.5]
        super(RNet, self).__init__(*param, **kwargs)

    def _init_net(self):
        # backend
        self.body = nn.Sequential(
            nn.Conv2d(3, 28, kernel_size=3, stride=1),  # conv1
            nn.PReLU(),  # prelu1
            nn.MaxPool2d(kernel_size=3, stride=2),  # pool1
            nn.Conv2d(28, 48, kernel_size=3, stride=1),  # conv2
            nn.PReLU(),  # prelu2
            nn.MaxPool2d(kernel_size=3, stride=2),  # pool2
            nn.Conv2d(48, 64, kernel_size=2, stride=1),  # conv3
            nn.PReLU()  # prelu3
        )

        self.fc = nn.Sequential(
            nn.Linear(64*2*2, 128),
            nn.PReLU()
        )
        # detection
        self.cls = nn.Sequential(
            nn.Linear(128, 1),
            nn.Sigmoid()
        )
        # bounding box regression
        self.box_offset = nn.Linear(128, 4)
        # lanbmark localization
        self.landmarks = nn.Linear(128, 10)

    def forward(self, x):
        # backend
        x = self.body(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)

        # detection
        det = self.cls(x)
        box = self.box_offset(x)
        landmarks = self.landmarks(x)

        return det, box, landmarks


class ONet(_Net):

    def __init__(self, **kwargs):
        # Hyper-parameter from original papaer
        param = [1, 0.5, 1]
        super(ONet, self).__init__(*param, **kwargs)

    def _init_net(self):
        # backend
        self.body = nn.Sequential(
            nn.Conv2d(3, 32, kernel_size=3, stride=1),  # conv1
            nn.PReLU(),  # prelu1
            nn.MaxPool2d(kernel_size=3, stride=2),  # pool1
            nn.Conv2d(32, 64, kernel_size=3, stride=1),  # conv2
            nn.PReLU(),  # prelu2
            nn.MaxPool2d(kernel_size=3, stride=2),  # pool2
            nn.Conv2d(64, 64, kernel_size=3, stride=1),  # conv3
            nn.PReLU(),  # prelu3
            nn.MaxPool2d(kernel_size=2, stride=2),  # pool3
            nn.Conv2d(64, 128, kernel_size=2, stride=1),  # conv4
            nn.PReLU()  # prelu4
        )

        self.fc = nn.Sequential(
            nn.Linear(128*2*2, 256),
            nn.PReLU()
        )
        # detection
        self.cls = nn.Sequential(
            nn.Linear(256, 1),
            nn.Sigmoid()
        )
        # bounding box regression
        self.box_offset = nn.Linear(256, 4)
        # lanbmark localization
        self.landmarks = nn.Linear(256, 10)
        # weight initiation weih xavier
        self.apply(weights_init)

    def forward(self, x):
        # backend
        x = self.body(x)
        x = x.view(x.size(0), -1)
        x = self.fc(x)

        # detection
        det = self.cls(x)

        # box regression
        box = self.box_offset(x)

        # landmarks regresion
        landmarks = self.landmarks(x)

        return det, box, landmarks

