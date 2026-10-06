# Quant Backtest Lab
# Architecture Reference Document

Version: 1.0

---

# IMPORTANT FOR AI AGENTS AND DEVELOPERS

This document is the official architecture reference for Quant Backtest Lab.

Before making any code changes:

1. Read this document first.
2. Understand the responsibilities of each layer.
3. Do not introduce changes that violate this architecture.
4. Do not add unnecessary complexity.
5. Prefer simple, modular, research-oriented solutions.

If a requested change conflicts with this document:
- explain the conflict first
- suggest the architectural impact
- do not silently change the architecture

The goal is to build a reusable backtesting research platform, not a single-strategy dashboard.

---

# Project Goal

Quant Backtest Lab is a platform for testing, analyzing, and archiving multiple trading strategies.

The system must support:

- multiple strategies
- reproducible backtests
- structured results
- saved reports
- future strategy expansion

The platform should allow a researcher to:

1. Add a strategy
2. Run a backtest
3. Analyze results
4. Save the experiment
5. Compare future runs

---

# Core Architecture

The system follows this flow:


Strategy
    |
    v
Strategy Runner
    |
    v
Backtest Engine
    |
    v
Backtest Result Contract
    |
    +----------------+
    |                |
    v                v
Frontend UI       Report Archive

Each layer has a specific responsibility.

---

# Layer Responsibilities

## 1. Strategy Layer

Location:


strategies/

Purpose:

Contains independent trading strategies.

A strategy is responsible for:

- generating signals
- defining parameters
- providing metadata


A strategy must NOT:

- execute trades
- calculate portfolio results
- modify market data
- know frontend logic


---

## 2. Strategy Interface

Every strategy should provide:

### Metadata

Information about the strategy:

- name
- version
- description


### Parameters

Configurable values:

Example:


fast_period
slow_period
risk_parameter


### Signal Generation

The strategy outputs signals only.

Example:


timestamp
direction
signal information

The strategy does not manage execution.

---

# 3. Backtest Engine

The engine is responsible for:

- receiving market data
- receiving signals
- executing trades
- managing positions
- calculating results


The engine must remain strategy-independent.

The engine should not know:

- EMA
- RSI
- price patterns
- strategy logic


Input:


Market Data + Signals

Output:


Backtest Result

---

# 4. Result Contract

The result contract is the communication layer between engine and UI.

Every backtest must produce a structured result.

Required information:

## Run Information

- run id
- date
- strategy name
- version


## Configuration

- symbol
- timeframe
- parameters


## Metrics

Examples:

- total trades
- win rate
- pnl
- drawdown
- profit factor
- expectancy


## Trades

Each trade should contain:

- entry
- exit
- direction
- pnl
- duration
- MAE
- MFE
- exit reason


## Equity

Contains:

- timestamp
- balance/equity
- cumulative pnl


## Data Quality

Contains:

- data source
- tick count
- candle count
- validation information

---

# 5. Frontend UI

The frontend is a visualization and analysis layer.

It should NOT calculate trading logic.

Responsibilities:

- display reports
- visualize charts
- inspect trades
- analyze results


The UI receives:


Backtest Result

and renders it.

---

# 6. Report Archive System

Every backtest run should be saved.

Recommended structure:


runs/
run_0001/

    config.json

    result.json

    trades.csv

    equity.csv

    report.html

    strategy.py


A saved run must be reproducible.

The user should be able to understand:

- which strategy was used
- what parameters were used
- what data was tested
- what result was produced

---

# 7. Data Rules

Market data is immutable.

Rules:

- strategies cannot modify raw data
- engine cannot alter source data
- reports reference the tested dataset