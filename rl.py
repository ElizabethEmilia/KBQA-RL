from dataset import Dataset
from policy_network import PolicyNet
from nets.lstm import GRU
from nets.perceptron import Perceptron
from env import Environment
from expeiment_settings import ExperimentSettings
from state import State
import torch
import numpy as np

class ReinforcementLearning:
    def __init__(self, dataset: Dataset, policy_net: PolicyNet, num_epochs, num_episode, steps,use_attention=False, use_perceptron=False):
        self.dataset = dataset
        self.epochs = num_epochs
        self.episodes = num_episode
        self.training = True
        self.steps = steps
        self.env = Environment(self.dataset.KG)

        self.policy_net = policy_net
        self.policy_net.embedder = self.dataset.embedder
        self.optimizer = torch.optim.Adam(policy_net.parameters(), lr=ExperimentSettings.learning_rate)
        self.initialize_nets()

    @property
    def KG(self):
        return self.dataset.KG

    def initialize_nets(self):
        self.gru = GRU(ExperimentSettings.dim, ExperimentSettings.dim // 2, 2, ExperimentSettings.dim)
        self.perceptron = Perceptron(ExperimentSettings.dim, ExperimentSettings.dim, 2*ExperimentSettings.dim)

    @property
    def T(self):
        return self.steps

    def train(self):
        self.training = True
        self.run()

    def test(self):
        self.training = False
        self.run()

    def run(self):
        total_acc, total_loss = [], []
        validate_acc, validate_loss = [], []

        epochs = self.epochs if self.training else 1
        for epoch in range(epochs):
            self.dataset.train(self.training)
            print('\n >>>>>> {}'.format('TRAINING: EPOCH {}\n'.format(epoch) if self.training else 'TESTING'))
            # Train
            for d in self.dataset:
                _, q, e_s, answer = d
                ## Only for display, they were tuned during each episode
                acc, loss = self.learn(q, e_s, answer, self.training)
                total_acc.append(acc)   # int
                total_loss.append(loss) # float

            print('## Training loss: {}, accuracy: {}'.format(np.mean(total_loss), np.mean(total_acc)))
            # Validate
            if not self.training:
                continue
            print('--- VALIDATING --\n')
            self.dataset.train(False)
            for d in self.dataset:
                _, q, e_s, answer = d
                ## Only for display, they were tuned during each episode
                acc, loss = self.learn(q, e_s, answer, False)
                validate_acc.append(acc)
                validate_loss.append(loss)

            print('## Validation loss: {}, accuracy: {}'.format(np.mean(validate_loss), np.mean(validate_acc)))

    def learn(self, q, e_s, answer, training):
        '''
        :param q: 问题 :n x d
        :param e_s: 头实体 :str
        :param answer: 答案 :str
        :param training:
        :return:
        '''
        self.optimizer.zero_grad()
        loss = torch.tensor(0.)
        correct_predictions = 0
        total_predictions = self.episodes
        for episode in range(self.episodes):
            reward_pool = []
            T = ExperimentSettings.max_T
            d = ExperimentSettings.dim
            n = len(q)

            ## 历史信息
            state_pool = {}      # S_t: T x State
            q_t = torch.zeros((T, d, n))          # q_t: T x n x d  问题池
            H_t = torch.zeros((T, d))             # H_t: T x d      历史信息
            action_pool = torch.zeros((T, d))     # r_t: T x d
            attention_wenghted_question_pool = torch.zeros((T, d))  # q_t_star: T x d
            #action_history = []
            initial_action = torch.zeros(d)
            H_t[0] = self.gru(initial_action)

            ## 初始化环境
            self.env.new_question(State(q, e_s, e_s, 0, q_t, H_t), answer)
            state_pool[0] = self.env.state
            episode_reward = torch.tensor(0.)
            rewards = []
            action_probs = []

            for t in range(T):
                # 更新问题
                question_t = self.perceptron(q, t)
                q_t[t] = question_t
                # 从环境获取动作空间
                possible_actions = self.env.get_possible_actions()
                if action_pool is None:
                    break
                action_space = self.beam_search(possible_actions)
                action_space, action_distribution = self.policy_net(action_space, q_t[t], H_t[t])
                if action_space is None:
                    break
                action = self.sample_action(action_space, action_distribution)
                #action_history.append(action)
                # TODO: 提前获取以适应Cuda
                action_pool[t] = self.dataset.embedder.get_relation_embedding(action)
                H_t[t + 1] = self.gru(action_pool[t])
                # 从环境获取奖励
                next_state, reward, reach_answer = self.env.step(action, question_t, H_t)
                episode_reward = ExperimentSettings.gamma * episode_reward + reward
                state_pool[t+1] = next_state
                rewards.append(reward)
                action_probs.append(action_probs)
                if reach_answer:
                    break
            prediction = state_pool[len(state_pool)].e_t
            if not rewards:
                return [prediction, None, None, None]
            action_probs = torch.stack(action_probs)
            if prediction == answer:
                correct_predictions = correct_predictions + 1
            ## TODO: log_prossibility loss function
            loss = loss - episode_reward
            if training:
                loss.backward()
        acc = correct_predictions / total_predictions
        if training:
            self.optimizer.step()
        return acc, loss

    def beam_search(self, possible_actions, beam_size=3):
        actions_scores = []
        for action in possible_actions:
            expected_reward = self.env.get_action_reward(action)
            actions_scores.append((action, expected_reward))

        sorted_actions = sorted(actions_scores, key=lambda x: x[1].item())[:beam_size]
        beamed_actions = [action_score[0] for action_score in sorted_actions]

        return beamed_actions

    def sample_action(self, action_space, action_distribution):
        idx = torch.multinomial(action_distribution, 1).item()
        return action_space[idx]

