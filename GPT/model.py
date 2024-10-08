import torch
import torch.nn as nn
import torch.nn.functional as f
import numpy as np


# padding된 토큰을 무시하기 위한 함수.
def padding_mask(input_q, input_k, pad_num):
    batch_size, len_q = input_q.size()
    batch_size, len_k = input_k.size()

    pad = input_k.detach().eq(pad_num).unsquezee(1).expand(batch_size, len_q, len_k)

    return pad


# decoder에서 보지 않는 단어는 mask하기 위한 함수.
def decoder_mask(seq):
    mask = torch.ones_like(seq).unsqueeze(-1).expand(seq.size(0), seq.size(1), seq.size(1))
    mask = mask.triu(diagonal=1)  # 행렬의 주대각선 위는 1로 채우고, 나머지는 0으로 채워서 다음 토큰을 마스크하게 함.
    return mask


# sinusodial에 기반해서 위치 임베딩을 계산하는 함수
def calcul_location(length, dimension):
    def angle(position, index):
        return position / np.power(10000, 2 * index / dimension)  # 삼각함수 내부에 들어갈 각을 계산해줌.

    def calcul_list(position):
        return [angle(position, i) for i in range(dimension)]  # 원소의 수를 은닉층의 차원만큼 형성하여 배열을 형성. 값은 삼각함수에 들어갈 값

    pos_embed = np.array([calcul_list(i) for i in range(length)])  # calcul_list를 이용해서 n_seq * d_hidn 크기의 2차원 배열을 제작.
    pos_embed[:, 0::2] = np.sin(pos_embed[:, 0::2])  # 짝수번째는 sin을 이용하여 값을 형성
    pos_embed[:, 1::2] = np.cos(pos_embed[:, 1::2])  # 홀수번째는 cos을 이용하여 값을 형성

    return pos_embed


# fully connected layer를 사용하기 위한 함수.
class Mlp(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.config = config

        self.dimension = config.d_hidn
        self.network = config.d_ff

        self.layer1 = nn.Linear(self.dimension, self.network)
        self.layer2 = nn.Linear(self.network, self.dimension)

        self.active = f.relu
        self.dropout = nn.Dropout(config.dropout)

    def forward(self, x):
        x = self.active(self.layer1(x))
        x = self.dropout(x)
        x = self.layer2(x)

        return x


# Q, K, V를 다 구했을때 attention을 구하기 위한 클래스.
class SelfAttention(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.scale = 1/self.config.d_head**0.5  # 차원의 제곱근으로 나눔. 정규화의 과정.

    def forward(self, q, k, v, attn_mask):
        score_matrix = torch.matmul(q, k.transpose(-1, -2)).mul(self.scale)
        # Q와 K의 내적을 해서 Query에 대한 답을 듣고, 내적한 결과에 정규화를 해줌.

        score_matrix.masked_fill_(attn_mask, -1e9)  # masking이 된 값에 음수를 넣어서 softmax를 한 후 값이 0이 되게 한다.
        attn_prob = nn.Softmax(dim=-1)(score_matrix)  # softmax를 사용해서 확률값으로 변환
        attention_matrix = torch.matmul(attn_prob, v)  # score_maxtrix와 V를 내적해서 attention_matrix를 구해서 어텐션 결과를 구함.

        return attention_matrix, attn_prob


# 입력으로부터 Q, K, V를 다 구하고, self attention으로부터 context를 구해온다.
class MultiHeadAttention(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.config = config

        # 입력 텐서를 각각 Q, K, V로 만들기 위한 선형 레이어.
        self.Q_weight = nn.Linear(self.config.d_hidn, self.config.d_head * self.config.n_head)
        self.K_weight = nn.Linear(self.config.d_hidn, self.config.d_head * self.config.n_head)
        self.V_weight = nn.Linear(self.config.d_hidn, self.config.d_head * self.config.n_head)

        self.scale_dot_product = SelfAttention(self.config)  # 가져온 Q, K, V를 사용해서 self attention을 계산하기 위한 함수를 가져옴.
        self.linear = nn.Linear(self.config.d_head * self.config.n_head, self.config.d_hidn)  # 계산한 Q,K,V의 차원을 줄이기 위함.

    def forward(self, pre_q, pre_k, pre_v, attn_mask):
        batch_size = pre_q.size(0)  # 한번에 처리할 입력 데이터 수

        q = self.Q_weight(pre_q).view(batch_size, -1, self.config.n_head, self.config.d_head).transpose(1, 2)
        k = self.K_weight(pre_k).view(batch_size, -1, self.config.n_head, self.config.d_head).transpose(1, 2)
        v = self.V_weight(pre_v).view(batch_size, -1, self.config.n_head, self.config.d_head).transpose(1, 2)
        # batch_size만큼 헤드를 분리하고, 차원에 따라 인식해서 혼자 처리함. 행렬의 열은 n_head, 행은 d_head임.
        # view까지만하면 메모리 내의 순서는 바뀌지 않아서 계산이 독립적으로 수행이 안됨. transpose 연산을 이용해서 차원 순서를 바꿔서 연산을 독립적으로 수행.

        attn_mask = attn_mask.unsqueeze(1).repeat(1, self.config.n_head, 1, 1)
        # attn_mask도 멀티 헤드 해주는 연산. unsqueeuze를 해줘서 1번째에 1 크기의 벡터를 추가하고
        # repeat을 통해서 (batch_size, n_head, 차원, 차원)으로 attn_mask의 차원을 변환.

        context, attn_prob = self.scale_dot_product(q, k, v, attn_mask)  # 계산한 Q, K, V를 셀프 어텐션 연산시킴.
        context = context.transpose(1, 2).contiguous().view(batch_size, -1, self.config.d_head*self.config.n_head)
        # attention matrix들을 concatenate하는 부분.

        context = self.linear(context)  # attention head의 원래 크기로 되돌리기 위해 W0를 곱해서 크기를 줄임.

        return context, attn_prob


# Decoder에서 사용할 하나의 DecoderBlock을 작성한 코드. 마찬가지로 순서에 맞게 가져온다.
class DecoderBlock(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.masked_attn = MultiHeadAttention(self.config)  # multi head attention을 위한 함수
        self.layer_norm1 = nn.LayerNorm(self.config.d_hidn, eps=self.config.layer_norm_epsilon)
        # masked multi head attention하고 정규화를 위함.
        self.layer_norm2 = nn.LayerNorm(self.config.d_hidn, eps=self.config.layer_norm_epsilon)
        # Feed forward neural net을 하고 정규화를 위함.
        self.feedforward = Mlp(self.config)  # feed forward를 위한 함수

    def forward(self, dec_inputs, attn_mask):
        attn_output, attn_prob = self.masked_attn(dec_inputs, dec_inputs, dec_inputs, attn_mask)
        attn_output = self.layer_norm1(dec_inputs + attn_output)
        output = self.feedforward(attn_output)
        output = self.layer_norm2(output + attn_output)

        return output, attn_prob


class Decoder(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.config = config

        self.input_embed = nn.Embedding(self.config.n_enc_vocab, self.config.d_hidn)  # 입력문장의 임베딩 벡터를 가져옴.
        pos_val = torch.FloatTensor(calcul_location(self.config.n_enc_seq + 1, self.config.d_hidn))  # 위치 임베딩을 계산.
        self.pos_embed = nn.Embedding.from_pretrained(pos_val, freeze=True)  # 위치 임베딩 값이 고정적이게 함.

        # decoderblock을 한 layer로 가짐.
        self.layers = nn.ModuleList([DecoderBlock(self.config) for _ in range(self.config.n_layer)])

    def forward(self, inputs):
        position = (torch.arange(inputs.size(1), device=inputs.size(), dtype=inputs.dtype).
                    expand(inputs.size(0), inputs.size(1)).contiguous() + 1)
        pos_mask = inputs.eq(self.config.i_pad)  # inputs에 값이 0인 원소가 있으면 pos_mask는 그 자리에 true을 넣고 아니면 false을 넣음.
        position.masked_fill(pos_mask, 0)  # pos_mask가 true인 위치에 0으로 채움.

        output = self.input_embed(inputs) + self.pos_embed(position)  # 워드 임베딩과 위치 임베딩을 더해줌.(따지고보면 입력값)
        dec_token_padding = padding_mask(inputs, inputs, self.config.i_pad)
        dec_next_padding = decoder_mask(inputs)
        dec_mask = torch.gt((dec_token_padding or dec_next_padding), 0)

        masked_probs = []
        for layer in self.layers:
            output, masked_prob = layer(output, dec_mask)
            masked_probs.append(masked_prob)

        return output, masked_probs


class GPT(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.config = config

        self.decoder = Decoder(self.config)

    def forward(self, dec_inputs):
        dec_outputs, dec_prob = self.decoder(dec_inputs)

        return dec_outputs, dec_prob

    def save(self, epoch, loss, path):
        torch.save({
            "epoch": epoch,
            "loss": loss,
            "state_dict": self.state_dict(),
        }, path)

    def load(self, path):
        save = torch.load(path)
        self.load_state_dict(save["state_dict"])

        return save["epoch"], save["loss"]


class pretrainGPT(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.config = config

        self.gpt = GPT(self.config)

        self.mapping = nn.Linear(self.config.d_hidn, self.config.n_enc_vocab, bias=False)
        self.mapping.weight = self.gpt.decoder.input_embed.weight

    def forward(self, dec_inputs):
        output, attn_prob = self.gpt(dec_inputs)
        logits = self.mapping(output)

        return logits[:, -1, :].contiguous(), attn_prob
