import torch
from torch.nn import MultiheadAttention, Conv1d, BatchNorm1d, Linear
from torch.nn.init import xavier_normal_
import torch.nn.functional as F
from torch.optim import Adam

def torch_it(tensor, device):
    return torch.as_tensor(tensor, device=torch.device(device), dtype=torch.float32)

class AttNet(torch.nn.Module):

    """
    MultiHead Attention Network
    """

    def __init__(self, trip_emb, embed_dim, num_heads=8, dropout=0.1, filter_inner=64):
        super(AttNet, self).__init__()

        # emb
        self.emb = Conv1d(trip_emb, embed_dim, 1)
        self.emb_bn = BatchNorm1d(embed_dim)

        # mha
        self.mha = MultiheadAttention(embed_dim,num_heads,dropout)
        self.mha_bn = BatchNorm1d(embed_dim)

        # ff
        self.inner = Conv1d(embed_dim,filter_inner,1)
        self.outer = Conv1d(filter_inner,embed_dim,1)
        self.ff_bn = BatchNorm1d(embed_dim)

        self.reset_parameters()

    def forward(self, input_):
        """
        input_: [batch_size, seq_length, trip_emb] float32
        """
        # emb
        input_ = input_.permute([0,2,1])
        emb_input = self.emb_bn(self.emb(input_)) #[batch_size, embed_dim, seq_length]
        emb_input = emb_input.permute([2,0,1]) #[seq_length, batch_size, embed_dim]

        # mha
        mha_output, _ = self.mha(emb_input, emb_input, emb_input) #[seq_length, batch_size, embed_dim]
        mha_output += emb_input
        mha_output = self.mha_bn(emb_input.permute([1,2,0])) #[batch_size, embed_dim, seq_length]

        # convff
        output = F.relu(self.inner(mha_output)) #[batch_size, filter_inner, seq_length]
        output = self.outer(output) #[batch_size, embed_dim, seq_length]
        output += mha_output
        output = self.ff_bn(output)

        # reduce
        output = torch.sum(output, 2)
        return output

    def reset_parameters(self):
        xavier_normal_(self.emb.weight)

class PolicyNetwork(torch.nn.Module):
    """
    Network for Policy estimator.
    """

    def __init__(self, trip_emb, emb_dim, hid_dim, n_act, n_ob_space):
        super(PolicyNetwork, self).__init__()

        self.attn = AttNet(trip_emb,emb_dim)
        self.hidden = Linear(emb_dim+n_ob_space, hid_dim)
        self.logits = Linear(hid_dim, n_act-1)

    def forward(self, state, emb_trip):
        #concat
        inputs = torch.cat((state, self.attn(emb_trip)), dim=1)

        #ff
        output = self.logits(F.relu(self.hidden(inputs)))

        #action probs
        probs = F.softmax(output, 1)

        return probs

class PolicyEstimator():
    """
    Policy Function approximator.
    """

    def __init__(self, learning_rate, trip_emb, emb_dim, hid_dim, n_act, n_ob_space):
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.policy = PolicyNetwork(trip_emb, emb_dim, hid_dim, n_act, n_ob_space).to(self.device)
        self.optim = Adam(self.policy.parameters(), learning_rate)

    def predict(self, state, emb_trip):
        state = torch_it(state, self.device)
        emb_trip = torch_it(emb_trip, self.device)
        return self.policy(state, emb_trip)

    def update(self, states, emb_trip, advantages, actions):
        states = torch_it(states, self.device)
        emb_trip = torch_it(emb_trip, self.device)
        advantages = torch_it(advantages, self.device)
        actions = torch_it(actions, self.device)

        # calculate loss
        log_probs = torch.log(self.predict(states, emb_trip))
        indices = (torch.arange(0, log_probs.shape[0]) * log_probs.shape[1]).to(self.device) + actions
        act_prob = torch.reshape(log_probs, (-1,))[indices.long()]
        loss = -torch.sum(act_prob*advantages)

        # complete update
        self.optim.zero_grad()
        loss.backward()
        self.optim.step()

        return loss
