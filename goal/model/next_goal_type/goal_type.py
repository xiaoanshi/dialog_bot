import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import optim
from torch.nn.utils import clip_grad_norm_
from torch.optim.lr_scheduler import CosineAnnealingLR
from torch.utils.data import Dataset, DataLoader
from tqdm import tqdm
from goal.model.next_goal_type.astar import AStarType
from goal.model.next_goal_type.config import Config
import os
os.environ['CUDA_VISIBLE_DEVICES'] = '1'


class GoalTypeDataset(Dataset):
    def __init__(self, tag):
        path_prefix = "../../data/train/"
        self.past_goal_seq = self.file_reader(path_prefix + tag + "_next_goal_type.txt")
        self.cur_goal = [goal_seq[-1] for goal_seq in self.past_goal_seq]
        self.last_goal_type = self.file_reader(path_prefix + tag + "_final_goal_type.txt")
        # self.session_id = self.file_reader(path_prefix + tag + "_next_goal_type_idx.txt")
        self.label = self.file_reader(path_prefix + tag + "_next_goal_type_label.txt")

    def file_reader(self, file_path):
        with open(file_path, "r", encoding='utf-8') as f:
            data = eval(f.read())
        return data

    def __getitem__(self, i):
        return self.past_goal_seq[i], self.cur_goal[i], self.last_goal_type[i], self.label[i]

    def __len__(self):
        return len(self.label)


def collate(batch):
    past_goal_seq = [item[0] for item in batch]
    cur_goal = torch.tensor([item[1] for item in batch], dtype=torch.long)
    last_goal_type = torch.tensor([item[2] for item in batch], dtype=torch.long)
    label = torch.tensor([item[3] for item in batch], dtype=torch.float)
    return past_goal_seq, cur_goal, last_goal_type, label


def train_epoch(model, criterion, optimizer, train_loader, device, max_norm, scheduler=None):
    model.train()
    total_loss = total_acc = total = 0
    progress_bar = tqdm(train_loader, desc='Training', leave=False)
    for past_goal_seq, cur_goal, last_goal_type, label in progress_bar:
        cur_goal = cur_goal.to(device)
        last_goal_type = last_goal_type.to(device)
        label = label.to(device)

        # Clean old gradients
        optimizer.zero_grad()

        # Forwards pass
        output = model(past_goal_seq, cur_goal, last_goal_type).squeeze()

        # Calculate how wrong the model is
        loss = criterion(output, label)
        prediction = (output > 0.5).long()

        # Perform gradient descent, backwards pass
        loss.backward()

        clip_grad_norm_(model.parameters(), max_norm=max_norm)

        # Take a step in the right direction
        optimizer.step()
        if scheduler:
            scheduler.step()

        # Record metrics
        total_loss += loss.item()
        total_acc += (prediction == label.long()).sum().float().item()
        total += len(label)

    return total_loss / total, total_acc / total


def validate_epoch(model, valid_loader, criterion, device):
    model.eval()
    total_loss = total_acc = total = 0
    with torch.no_grad():
        progress_bar = tqdm(valid_loader, desc='Validating', leave=False)
        for past_goal_seq, cur_goal, last_goal_type, label in progress_bar:
            cur_goal = cur_goal.to(device)
            last_goal_type = last_goal_type.to(device)
            label = label.to(device)
            # Forwards pass
            output = model(past_goal_seq, cur_goal, last_goal_type).squeeze()

            # Calculate how wrong the model is
            loss = criterion(output, label)
            # print(output)
            prediction = (output > 0.5).long()
            # print(prediction)
            # break
            # Record metrics
            total_loss += loss.item()
            total_acc += (prediction == label.long()).sum().float().item()
            total += len(label)

    return total_loss / total, total_acc / total


def main(config):
    train_loader = DataLoader(GoalTypeDataset("train"), batch_size=config.batch_size, collate_fn=collate, shuffle=True)
    valid_loader = DataLoader(GoalTypeDataset("val"), batch_size=config.batch_size, collate_fn=collate, shuffle=True)
    model = AStarType(config).to(config.device)
    criterion = nn.BCELoss()
    embedding_params_ids = list(map(id, model.goal_embedding.parameters()))
    rest_params = filter(lambda x: id(x) not in embedding_params_ids, model.parameters())
    optimizer = optim.Adam(
        [{'params': filter(lambda p: p.requires_grad, rest_params)},
         {'params': model.goal_embedding.parameters(), 'lr': 0.5}],
        lr=config.lr,
    )
    scheduler = CosineAnnealingLR(optimizer, 32)

    train_losses, valid_losses, min_valid_loss = [], [], float('inf')
    for epoch in range(config.num_epoch):
        train_loss, train_acc = train_epoch(model, criterion, optimizer, train_loader, config.device, config.max_norm, scheduler=scheduler)
        valid_loss, valid_acc = validate_epoch(model, valid_loader, criterion, config.device)
        # break
        tqdm.write(
            f'epoch #{epoch + 1:3d}\ttrain_loss: {train_loss:.3e}'
            f' train_acc: {train_acc:.4f}'
            f' valid_loss: {valid_loss:.3e}'
            f' valid_acc: {valid_acc:.4f}\n'
        )

        # Early stopping if the current valid_loss is greater than the last three valid losses
        if len(valid_losses) > 2 and all(valid_loss >= loss for loss in valid_losses[-3:]):
            print('Stop early')
            break

        if valid_loss < min_valid_loss:
            min_valid_loss = valid_loss
            torch.save(model.state_dict(), config.save_path)

        train_losses.append(train_loss)
        valid_losses.append(valid_loss)


if __name__ == '__main__':
    config = Config()
    main(config)
