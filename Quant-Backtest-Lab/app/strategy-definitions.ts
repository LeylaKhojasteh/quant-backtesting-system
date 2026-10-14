/** Strategy metadata for the fixture strategy-settings dialog. */
export const strategyDefinitions = {
  'Candle Direction Test': {
    description: 'Bullish candles generate LONG signals; bearish candles generate SHORT signals.',
    inputs: [
      { key: 'stop', label: 'Stop Distance', type: 'float', defaultValue: 5, min: 0.1, step: 0.1 },
      { key: 'take', label: 'Take Profit Distance', type: 'float', defaultValue: 10, min: 0.1, step: 0.1 },
    ],
  },
};
