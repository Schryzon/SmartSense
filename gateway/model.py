import os
import threading
import torch
import torch.nn as nn
import torch.optim as optim
import pandas as pd
import numpy as np
from sklearn.preprocessing import MinMaxScaler

# check for GPU (CUDA) support
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Comfort forecasting will run on: {device}")

# comfort score computation based on deviations from ideal comfort (28°C, 50% RH)
def calculate_comfort_score(temp: float, hum: float) -> float:
    temp_penalty = abs(temp - 28.0) * 1.2
    hum_penalty = abs(hum - 50.0) * 0.08
    score = 10.0 - (temp_penalty + hum_penalty)
    return max(0.0, min(10.0, score))

# PyTorch LSTM forecasting model (Seq2Seq architecture)
class TelemetryLSTM(nn.Module):
    def __init__(self, input_dim: int = 3, hidden_dim: int = 32, num_layers: int = 1, output_seq_len: int = 5, output_dim: int = 2):
        super(TelemetryLSTM, self).__init__()
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.output_seq_len = output_seq_len
        self.output_dim = output_dim
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_dim, output_seq_len * output_dim)

    def forward(self, x):
        # x: (batch_size, seq_len, input_dim)
        h0 = torch.zeros(self.num_layers, x.size(0), self.hidden_dim, device=x.device)
        c0 = torch.zeros(self.num_layers, x.size(0), self.hidden_dim, device=x.device)
        
        out, _ = self.lstm(x, (h0, c0))
        # use output of last time step
        out = out[:, -1, :]
        out = self.fc(out)
        # reshape to (batch_size, output_seq_len, output_dim)
        out = out.view(-1, self.output_seq_len, self.output_dim)
        return out

# PyTorch Feed-Forward Autoencoder for Anomaly Detection
class TelemetryAnomalyDetector(nn.Module):
    def __init__(self, input_dim: int = 3, hidden_dim: int = 8):
        super(TelemetryAnomalyDetector, self).__init__()
        self.encoder = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 4),
            nn.ReLU()
        )
        self.decoder = nn.Sequential(
            nn.Linear(4, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, input_dim)
        )

    def forward(self, x):
        return self.decoder(self.encoder(x))

class ForecastingPipeline:
    def __init__(self, window_size: int = 10, output_seq_len: int = 5):
        self.window_size = window_size
        self.output_seq_len = output_seq_len
        self.model = TelemetryLSTM(output_seq_len=output_seq_len).to(device)
        self.ae_model = TelemetryAnomalyDetector().to(device)
        self.scaler = MinMaxScaler()
        self.is_trained = False
        self.model_path = os.path.join(os.path.dirname(__file__), "model.pt")
        self.lock = threading.Lock()
        
        # dynamic z-score drift tracking params
        self.train_mean = None
        self.train_std = None

        # try loading pre-trained weights if they exist
        if os.path.exists(self.model_path):
            try:
                checkpoint = torch.load(self.model_path, map_location=device)
                if isinstance(checkpoint, dict) and "lstm" in checkpoint:
                    self.model.load_state_dict(checkpoint["lstm"])
                    self.ae_model.load_state_dict(checkpoint["ae"])
                    self.train_mean = checkpoint.get("train_mean")
                    self.train_std = checkpoint.get("train_std")
                else:
                    # backward compatibility: load raw state dict to lstm
                    self.model.load_state_dict(checkpoint)
                self.is_trained = True
                print("loaded pre-trained weights successfully.")
            except Exception as e:
                print(f"could not load state dict (shapes probably mismatch): {e}")

    def prepare_data(self, df: pd.DataFrame):
        # we predict temperature and humidity based on [temperature, humidity, occupied]
        data = df[["temperature", "humidity", "occupied"]].values
        scaled_data = self.scaler.fit_transform(data)

        x, y = [], []
        # target sequence starts right after window_size up to output_seq_len
        for i in range(len(scaled_data) - self.window_size - self.output_seq_len + 1):
            x.append(scaled_data[i : i + self.window_size])
            y.append(scaled_data[i + self.window_size : i + self.window_size + self.output_seq_len, :2])

        return torch.tensor(np.array(x), dtype=torch.float32), torch.tensor(np.array(y), dtype=torch.float32)

    def train(self, df: pd.DataFrame, epochs: int = 100, lr: float = 0.01) -> bool:
        if df is None or len(df) < self.window_size + self.output_seq_len + 5:
            # not enough data points
            print(f"insufficient data points for training: {0 if df is None else len(df)}")
            return False

        print("training PyTorch models (LSTM + Autoencoder) on GPU...")
        
        # calculate mean/std for drift detection Z-scores
        self.train_mean = df[["temperature", "humidity"]].mean().values
        self.train_std = df[["temperature", "humidity"]].std().values

        x, y = self.prepare_data(df)
        
        # Prepare inputs for the autoencoder (all historical steps)
        ae_data = df[["temperature", "humidity", "occupied"]].values
        ae_data_scaled = self.scaler.transform(ae_data)
        ae_x = torch.tensor(ae_data_scaled, dtype=torch.float32).to(device)

        # move tensors to GPU
        x = x.to(device)
        y = y.to(device)

        # LSTM loss & optimizer
        criterion = nn.MSELoss()
        optimizer = optim.Adam(self.model.parameters(), lr=lr)

        # Autoencoder loss & optimizer
        ae_criterion = nn.MSELoss()
        ae_optimizer = optim.Adam(self.ae_model.parameters(), lr=lr)

        with self.lock:
            # Train LSTM
            self.model.train()
            for epoch in range(epochs):
                optimizer.zero_grad()
                outputs = self.model(x)
                loss = criterion(outputs, y)
                loss.backward()
                optimizer.step()

            # Train Autoencoder
            self.ae_model.train()
            for epoch in range(epochs):
                ae_optimizer.zero_grad()
                ae_outputs = self.ae_model(ae_x)
                ae_loss = ae_criterion(ae_outputs, ae_x)
                ae_loss.backward()
                ae_optimizer.step()

            # save combined checkpoint weights
            checkpoint = {
                "lstm": self.model.state_dict(),
                "ae": self.ae_model.state_dict(),
                "train_mean": self.train_mean,
                "train_std": self.train_std
            }
            torch.save(checkpoint, self.model_path)
            self.is_trained = True
            print(f"training completed. LSTM Loss: {loss.item():.6f}, AE Loss: {ae_loss.item():.6f}")
        return True

    def predict_next(self, recent_data: list) -> list:
        """
        recent_data: list of dicts/lists containing [temp, hum, occupied] of length >= window_size
        returns: list of [predicted_temp, predicted_hum] for the next output_seq_len steps
        """
        if not self.is_trained or len(recent_data) < self.window_size:
            # fallback: replicate the last observed point
            print("falling back (insufficient data or model untrained)")
            last_point = recent_data[-1]
            return [[float(last_point[0]), float(last_point[1])] for _ in range(self.output_seq_len)]

        # extract last N steps
        slice_data = np.array(recent_data[-self.window_size:])[:, :3]
        
        # scale features using fitted scaler
        scaled_slice = self.scaler.transform(slice_data)
        
        # format input tensor
        input_tensor = torch.tensor(scaled_slice, dtype=torch.float32).unsqueeze(0).to(device)

        with self.lock:
            self.model.eval()
            with torch.no_grad():
                # shape: (1, output_seq_len, 2)
                prediction_scaled = self.model(input_tensor).cpu().numpy()[0]

        # inverse scale each predicted step
        predictions = []
        for i in range(self.output_seq_len):
            dummy_row = np.zeros(3)
            dummy_row[0] = prediction_scaled[i, 0]
            dummy_row[1] = prediction_scaled[i, 1]
            inversed = self.scaler.inverse_transform(dummy_row.reshape(1, -1))[0]
            predictions.append([float(inversed[0]), float(inversed[1])])
            
        return predictions

    def detect_anomaly(self, single_reading: list) -> tuple:
        """
        single_reading: [temp, hum, occupied]
        returns: (is_anomaly, reconstruction_loss)
        """
        if not self.is_trained:
            return False, 0.0

        scaled = self.scaler.transform(np.array([single_reading]))
        tensor = torch.tensor(scaled, dtype=torch.float32).to(device)

        with self.lock:
            self.ae_model.eval()
            with torch.no_grad():
                reconstructed = self.ae_model(tensor)
                # compute mean squared reconstruction loss
                loss = torch.mean((reconstructed - tensor) ** 2).item()

        # wtf is this value, let's use 0.08 as dynamic warning boundary
        is_anomaly = loss > 0.08
        return is_anomaly, float(loss)
