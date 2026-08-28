import os
import pandas as pd
import pandas_ta as ta
import numpy as np
import ccxt
import torch as th
from stable_baselines3 import PPO
from stable_baselines3.common.env_util import make_vec_env
import gymnasium as gym
from gymnasium import spaces

MODEL_PATH = "btc_deep_quant_pro.zip"
ONNX_PATH = "btc_ai_model.onnx"
SYMBOL = 'BTC/USDT'
TIMEFRAME = '5m'
WINDOW_SIZE = 60

class ProQuantEnv(gym.Env):
    def __init__(self, df):
        super(ProQuantEnv, self).__init__()
        self.df = df
        self.window_size = WINDOW_SIZE
        self.max_steps = len(self.df) - 1
        self.action_space = spaces.Discrete(3)
        self.obs_features = ['close_pct', 'EMA_5_pct', 'EMA_12_pct', 'EMA_200_pct', 'RSI_norm', 'MACD_norm', 'volume_norm']
        self.observation_space = spaces.Box(low=-10, high=10, shape=((len(self.obs_features) * self.window_size) + 2,), dtype=np.float32)

    def reset(self, seed=None):
        super().reset(seed=seed)
        self.current_step = self.window_size
        self.is_holding = 0.0
        self.entry_price = 0.0
        return self._get_observation(), {}

    def _get_observation(self):
        window_data = self.df.loc[self.current_step - self.window_size + 1 : self.current_step, self.obs_features].values
        unrealized_pnl = ((self.df.loc[self.current_step, 'close'] - self.entry_price) / self.entry_price * 100) if self.is_holding else 0.0
        return np.concatenate((window_data.flatten(), np.array([self.is_holding, unrealized_pnl]))).astype(np.float32)

    def step(self, action):
        current_price = self.df.loc[self.current_step, 'close']
        reward = 0
        if action == 1 and self.is_holding == 0:
            self.is_holding = 1; self.entry_price = current_price; reward = 0.01
        elif action == 2 and self.is_holding == 1:
            self.is_holding = 0
            profit = (current_price - self.entry_price) / self.entry_price
            reward = profit * 3000.0 if profit > 0.003 else (profit * 500.0 - 1.0)
            self.entry_price = 0.0
        elif action == 0 and self.is_holding == 0: reward = -0.005
        if self.is_holding == 1: reward -= 0.001
        self.current_step += 1
        return self._get_observation(), float(reward), self.current_step >= self.max_steps, False, {}

def prepare_data(df):
    df.ta.ema(length=5, append=True); df.ta.ema(length=12, append=True); df.ta.ema(length=200, append=True)
    df.ta.rsi(length=21, append=True); df.ta.macd(fast=12, slow=26, signal=9, append=True); df.ta.atr(length=14, append=True)
    for col in ['open', 'high', 'low', 'close', 'EMA_5', 'EMA_12', 'EMA_200']: df[f'{col}_pct'] = df[col].pct_change() * 100
    df['volume_norm'] = df['volume'] / df['volume'].rolling(50).max()
    df['RSI_norm'] = df['RSI_21'] / 100.0
    df['MACD_norm'] = np.tanh(df['MACD_12_26_9'])
    return df.dropna().reset_index(drop=True)

print("📥 Fetching latest market data...")
exchange = ccxt.mexc()
ohlcv = exchange.fetch_ohlcv(SYMBOL, TIMEFRAME, limit=2000)
df = pd.DataFrame(ohlcv[:-1], columns=['t', 'open', 'high', 'low', 'close', 'volume'])
df = prepare_data(df)

if os.path.exists(MODEL_PATH):
    print("🧠 Loading model for daily training...")
    model = PPO.load(MODEL_PATH)
    model.set_env(make_vec_env(lambda: ProQuantEnv(df), n_envs=1))
    print("🚀 Training started (15,000 steps)...")
    model.learn(total_timesteps=15000)
    model.save(MODEL_PATH)
    print("✅ Training complete! Updating ONNX file...")
    
    class OnnxablePolicy(th.nn.Module):
        def __init__(self, policy):
            super().__init__()
            self.policy = policy
        def forward(self, observation):
            features = self.policy.extract_features(observation)
            latent_pi, _ = self.policy.mlp_extractor(features)
            logits = self.policy.action_net(latent_pi)
            return th.argmax(logits, dim=1)

    onnx_policy = OnnxablePolicy(model.policy)
    dummy_input = th.randn(1, 422)
    th.onnx.export(onnx_policy, dummy_input, ONNX_PATH, opset_version=11, input_names=["input"], output_names=["output"])
    print("✅ MLOps Pipeline Complete. ONNX Ready.")
