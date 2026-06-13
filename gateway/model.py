import os
import torch
import torch.nn as nn
import torch.optim as optim
import pandas as pd
import numpy as np
from sklearn.preprocessing import MinMaxScaler

# check for GPU (CUDA) support
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Comfort forecasting will run on: {device}")

# comfort score computation based on deviations from ideal comfort (22°C, 50% RH)
def calculate_comfort_score(temp: float, hum: float) -> float:
    temp_penalty = abs(temp - 22.0) * 1.2
    hum_penalty = abs(hum - 50.0) * 0.08
    score = 10.0 - (temp_penalty + hum_penalty)
    return max(0.0, min(10.0, score))

# PyTorch LSTM forecasting model
class TelemetryLSTM(nn.Module):
    def __init__(self, input_dim: int = 3, hidden_dim: int = 32, num_layers: int = 1, output_dim: int = 2):
        super(TelemetryLSTM, self).__init__()
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.lstm = nn.LSTM(input_dim, hidden_dim, num_layers, batch_first=True)
        self.fc = nn.Linear(hidden_dim, output_dim)

    def forward(self, x):
        # x: (batch_size, seq_len, input_dim)
        h0 = torch.zeros(self.num_layers, x.size(0), self.hidden_dim, device=x.device)
        c0 = torch.zeros(self.num_layers, x.size(0), self.hidden_dim, device=x.device)
        
        out, _ = self.lstm(x, (h0, c0))
        # use output of last time step
        out = out[:, -1, :]
        out = self.fc(out)
        return out

class ForecastingPipeline:
    def __init__(self, window_size: int = 10):
        self.window_size = window_size
        self.model = TelemetryLSTM().to(device)
        self.scaler = MinMaxScaler()
        self.is_trained = False
        self.model_path = os.path.join(os.path.dirname(__file__), "model.pt")

        # try loading pre-trained weights if they exist
        if os.path.exists(self.model_path):
            try:
                self.model.load_state_dict(torch.load(self.model_path, map_location=device))
                self.is_trained = True
                print("loaded pre-trained weights.")
            except Exception as e:
                print(f"could not load state dict: {e}")

    def prepare_data(self, df: pd.DataFrame):
        # we predict temperature and humidity based on [temperature, humidity, occupied]
        data = df[["temperature", "humidity", "occupied"]].values
        scaled_data = self.scaler.fit_transform(data)

        x, y = [], []
        for i in range(len(scaled_data) - self.window_size):
            x.append(scaled_data[i : i + self.window_size])
            # target is [temperature, humidity] of the next step
            y.append(scaled_data[i + self.window_size, :2])

        return torch.tensor(np.array(x), dtype=torch.float32), torch.tensor(np.array(y), dtype=torch.float32)

    def train(self, df: pd.DataFrame, epochs: int = 100, lr: float = 0.01) -> bool:
        if df is None or len(df) < self.window_size + 5:
            # not enough data points
            print(f"insufficient data points for training: {0 if df is None else len(df)}")
            return False

        print("training PyTorch model on GPU...")
        x, y = self.prepare_data(df)
        
        # move tensors to GPU
        x = x.to(device)
        y = y.to(device)

        criterion = nn.MSELoss()
        optimizer = optim.Adam(self.model.parameters(), lr=lr)

        self.model.train()
        for epoch in range(epochs):
            optimizer.zero_grad()
            outputs = self.model(x)
            loss = criterion(outputs, y)
            loss.backward()
            optimizer.step()

        # save weights
        torch.save(self.model.state_dict(), self.model_path)
        self.is_trained = True
        print(f"training completed. Loss: {loss.item():.6f}")
        return True

    def predict_next(self, recent_data: list) -> tuple:
        """
        recent_data: list of dicts/lists containing [temp, hum, occupied] of length >= window_size
        returns: (predicted_temp, predicted_hum)
        """
        if not self.is_trained or len(recent_data) < self.window_size:
            # fallback to simple linear extrapolation if not enough data or model not trained
            print("falling back to linear projection (insufficient data or model untrained)")
            last_point = recent_data[-1]
            return float(last_point[0]), float(last_point[1])

        # extract last N steps
        slice_data = np.array(recent_data[-self.window_size:])[:, :3]
        
        # scale features using fitted scaler
        scaled_slice = self.scaler.transform(slice_data)
        
        # format input tensor
        input_tensor = torch.tensor(scaled_slice, dtype=torch.float32).unsqueeze(0).to(device)

        self.model.eval()
        with torch.no_grad():
            prediction_scaled = self.model(input_tensor).cpu().numpy()[0]

        # inverse scale the prediction (dummy fill for 'occupied' feature since transform expects 3 features)
        dummy_row = np.zeros(3)
        dummy_row[0] = prediction_scaled[0]
        dummy_row[1] = prediction_scaled[1]
        
        inversed = self.scaler.inverse_transform(dummy_row.reshape(1, -1))[0]
        
        return float(inversed[0]), float(inversed[1])
