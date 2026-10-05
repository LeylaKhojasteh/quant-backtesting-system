import {trades,cumulative,fmt} from './demo';
export type ReportMetric={label:string;value:string;note?:string;pending?:boolean;group?:string};
const sum=(v:number[])=>v.reduce((a,b)=>a+b,0);
const avg=(v:number[])=>sum(v)/v.length;
const wins=trades.filter(t=>t.pnl>0),losses=trades.filter(t=>t.pnl<0);
const longs=trades.filter(t=>t.direction==='LONG'),shorts=trades.filter(t=>t.direction==='SHORT');
const profit=sum(wins.map(t=>t.pnl)),loss=sum(losses.map(t=>t.pnl));
const durations=trades.map(t=>(t.exitTime-t.entryTime)/60000);
const dd=cumulative.map((v,i)=>Math.max(0,...cumulative.slice(0,i+1))-v);
const streaks:{win:boolean;count:number;pnl:number}[]=[];
for(const t of trades){const win=t.pnl>0,last=streaks.at(-1);if(last?.win===win){last.count++;last.pnl+=t.pnl}else streaks.push({win,count:1,pnl:t.pnl});}
const winRuns=streaks.filter(s=>s.win),lossRuns=streaks.filter(s=>!s.win);
const metric=(label:string,value:string,note='Seeded trades · price terms',group?:string):ReportMetric=>({label,value,note,group});
const pending=(label:string,note:string,group?:string):ReportMetric=>({label,value:'Pending',pending:true,note,group});
const count=(items:typeof trades)=>`${items.length} (${(items.filter(t=>t.pnl>0).length/items.length*100).toFixed(2)}% won)`;
const dur=(v:number)=>`${Math.floor(v/60)}h ${Math.floor(v%60)}m ${Math.round(v%1*60)}s`;
export const stats={profit,loss,net:profit+loss,profitFactor:profit/Math.abs(loss),payoff:avg(trades.map(t=>t.pnl)),maxDD:Math.max(...dd),avgWin:avg(wins.map(t=>t.pnl)),avgLoss:avg(losses.map(t=>t.pnl)),avgDuration:avg(durations),exposure:sum(durations)/(22*60)*100,avgR:avg(trades.map(t=>t.pnl/5)),avgSpread:avg(trades.map(t=>t.spreadIn)),winRate:wins.length/trades.length*100};
export const runContext={symbol:'XAUUSD',period:'M15',window:'2025-01-31 00:00 → 22:00 UTC',bars:'88',ticks:'322,548'};
export const reportGroups:Record<string,ReportMetric[]>={
 'Run & Data':[
  metric('Strategy','Candle Direction Test','Demo strategy','Run Scope'),
  metric('Symbol / Period',`${runContext.symbol} / ${runContext.period}`,runContext.window,'Run Scope'),
  metric('Inputs','Stop 5.0 · TP 10.0','Reference result inputs; current configured inputs appear in the run strip','Run Scope'),
  metric('Bars',runContext.bars,'Seeded M15 candles','Dataset'),
  metric('Ticks',runContext.ticks,'Demo range tick count','Dataset'),
  metric('Symbols','1','Single-symbol demo','Dataset'),
  pending('History Quality','Requires one-minute data and gap validation; not inferred from Modeling Quality.','Dataset'),
  metric('Modeling Quality','99.12%','Demo value; the production definition is still pending','Dataset'),
  metric('Initial Deposit','10,000.00','Demo balance projection only; no account-currency model','Account'),
  metric('Withdrawal','None','No withdrawals in this demo','Account'),
  metric('Balance','9,984.18','Demo projection only; initial 10,000 + price-term PnL','Account'),
  pending('Currency / Leverage','No account configuration in the existing price-term engine.','Account'),
 ],
 'Profit & Risk':[
  metric('Total Net Profit',fmt(stats.net),undefined,'Net Result'),
  metric('Gross Profit',fmt(profit),undefined,'Net Result'),
  metric('Gross Loss',fmt(loss),undefined,'Net Result'),
  metric('Profit Factor',fmt(stats.profitFactor),'Gross profit / absolute gross loss · dimensionless','Net Result'),
  metric('Expected Payoff',fmt(stats.payoff),undefined,'Net Result'),
  metric('Return %',((stats.net/10000)*100).toFixed(2)+'%','Net PnL against the 10,000 demo account base','Net Result'),
  metric('Balance Drawdown Absolute',fmt(-Math.min(0,...cumulative)),'Closed-trade PnL projection · price terms','Drawdown'),
  metric('Balance Drawdown Maximal',fmt(stats.maxDD),'Closed-trade PnL projection · price terms','Drawdown'),
  pending('Balance Drawdown Relative','Requires account-currency balance, lot sizing and initial deposit.','Drawdown'),
  pending('Equity Drawdown Absolute','Requires intratrade marked-to-market equity.','Drawdown'),
  pending('Equity Drawdown Maximal','Requires intratrade marked-to-market equity.','Drawdown'),
  pending('Equity Drawdown Relative','Requires account-currency marked-to-market equity.','Drawdown'),
  metric('Recovery Factor',fmt(stats.net/stats.maxDD),'Net price PnL / maximal closed-trade drawdown','Drawdown'),
  pending('Margin Level','Requires a real margin model. No simulated margin value is shown.','Drawdown'),
 ],
 'Trade Statistics':[
  metric('Total Trades','7','Seeded trade count','Sample'),
  pending('Total Deals','Trade log has no deal-level record; do not assume two deals per trade.','Sample'),
  metric('Short Trades (won %)',count(shorts),'3 shorts; none profitable','Sample'),
  metric('Long Trades (won %)',count(longs),'4 longs; 1 profitable','Sample'),
  metric('Profit Trades (% of total)',`${wins.length} (${stats.winRate.toFixed(2)}%)`,'Seeded trade count','Outcome'),
  metric('Loss Trades (% of total)',`${losses.length} (${(losses.length/7*100).toFixed(2)}%)`,'Seeded trade count','Outcome'),
  metric('Largest Profit Trade',fmt(Math.max(...wins.map(t=>t.pnl))),undefined,'Outcome'),
  metric('Largest Loss Trade',fmt(Math.min(...losses.map(t=>t.pnl))),undefined,'Outcome'),
  metric('Average Profit Trade',fmt(stats.avgWin),undefined,'Outcome'),
  metric('Average Loss Trade',fmt(stats.avgLoss),undefined,'Outcome'),
  metric('Minimal Position Holding Time',dur(Math.min(...durations)),'Entry to exit · UTC','Holding Time'),
  metric('Maximal Position Holding Time',dur(Math.max(...durations)),'Entry to exit · UTC','Holding Time'),
  metric('Average Position Holding Time',dur(stats.avgDuration),'Entry to exit · UTC','Holding Time'),
 ],
 'Sequences & Ratios':[
  metric('Maximum Consecutive Wins',`${Math.max(...winRuns.map(s=>s.count))} (${fmt(winRuns.sort((a,b)=>b.count-a.count)[0].pnl)})`,'Count (price-term total)','Streaks'),
  metric('Maximum Consecutive Losses',`${Math.max(...lossRuns.map(s=>s.count))} (${fmt(lossRuns.sort((a,b)=>b.count-a.count)[0].pnl)})`,'Count (price-term total)','Streaks'),
  metric('Maximal Consecutive Profit',`${fmt(Math.max(...winRuns.map(s=>s.pnl)))} (${winRuns.sort((a,b)=>b.pnl-a.pnl)[0].count})`,'Price-term total (count)','Streaks'),
  metric('Maximal Consecutive Loss',`${fmt(Math.min(...lossRuns.map(s=>s.pnl)))} (${lossRuns.sort((a,b)=>a.pnl-b.pnl)[0].count})`,'Price-term total (count)','Streaks'),
  metric('Average Consecutive Wins',fmt(avg(winRuns.map(s=>s.count))),'Average winning run length','Streaks'),
  metric('Average Consecutive Losses',fmt(avg(lossRuns.map(s=>s.count))),'Average losing run length','Streaks'),
  pending('Sharpe Ratio','Needs a defined account return series and observation frequency; not estimated from seven price-term trades.','Statistical Ratios'),
  pending('AHPR','Requires account equity changes per trade.','Statistical Ratios'),
  pending('GHPR','Requires account equity changes per trade.','Statistical Ratios'),
  pending('Z-Score','Trade dependence statistic needs a defined statistical implementation and adequate sample.','Statistical Ratios'),
  pending('Balance Regression Correlation','Requires an account balance series.','Statistical Ratios'),
  pending('Balance Regression Error','Requires an account balance series in deposit currency.','Statistical Ratios'),
  pending('Custom Tester Output','The Python engine exposes no per-run tester callback.','Statistical Ratios'),
 ],
 'Excursions & Execution':[
  pending('Correlation (Profits, MFE)','Requires observed favorable excursions for every trade.','Excursion Correlation'),
  pending('Correlation (Profits, MAE)','Requires observed adverse excursions for every trade.','Excursion Correlation'),
  pending('Correlation (MFE, MAE)','Requires both observed excursion series.','Excursion Correlation'),
  metric('TP Exits','1 (14.29%)','Seeded exit reasons','Execution'),
  metric('SL Exits','5 (71.43%)','Seeded exit reasons','Execution'),
  metric('End of Data','1 (14.29%)','Seeded exit reasons','Execution'),
  metric('Average Entry Spread',fmt(stats.avgSpread),'Seeded observed entry ticks · price terms','Execution'),
  metric('Average Exit Spread',fmt(avg(trades.map(t=>t.spreadOut))),'Seeded observed exit ticks · price terms','Execution'),
  metric('Signal → Entry Delay','124 ms','Signal available to first eligible entry tick','Execution'),
  metric('Fill-side Checks','14 / 14 pass','7 entries + 7 exits checked against observed BID / ASK','Execution'),
  metric('Execution Model','Observed BID / ASK','Reference tick-side semantics only; Python not connected','Execution'),
 ]
};
export const diagramNames=['Entries by Hour','Entries by Weekday','Entries by Month','Profit / Loss by Hour','Profit / Loss by Weekday','Profit / Loss by Month','MFE vs Profit','MAE vs Profit','Profit vs Holding Time','PnL by Direction','Exit Reasons','R-Multiple Distribution'];
export function diagramData(name:string){
 if(name==='MFE vs Profit'||name==='MAE vs Profit')return [];
 if(name==='Profit vs Holding Time')return trades.map((t,i)=>({label:`#${t.id}`,x:durations[i],v:t.pnl,n:1}));
 if(name==='PnL by Direction')return ['LONG','SHORT'].map(d=>({label:d,x:0,v:sum(trades.filter(t=>t.direction===d).map(t=>t.pnl)),n:trades.filter(t=>t.direction===d).length}));
 if(name==='Exit Reasons')return ['TP','SL','EOD'].map(d=>({label:d,x:0,v:trades.filter(t=>t.reason===d).length,n:trades.filter(t=>t.reason===d).length}));
 if(name==='R-Multiple Distribution')return trades.map(t=>({label:`#${t.id}`,x:t.id,v:t.pnl/5,n:1}));
 const entry=name.startsWith('Entries'),hour=name.endsWith('Hour'),weekday=name.endsWith('Weekday');
 const labels=hour?Array.from({length:24},(_,i)=>`${String(i).padStart(2,'0')}:00`):weekday?['Sun','Mon','Tue','Wed','Thu','Fri','Sat']:['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
 return labels.map((label,x)=>{const rows=trades.filter(t=>{const d=new Date(entry?t.entryTime:t.exitTime);return(hour?d.getUTCHours():weekday?d.getUTCDay():d.getUTCMonth())===x});return{label,x,v:entry?rows.length:sum(rows.map(t=>t.pnl)),positive:entry?rows.length:sum(rows.filter(t=>t.pnl>0).map(t=>t.pnl)),negative:entry?0:sum(rows.filter(t=>t.pnl<0).map(t=>t.pnl)),n:rows.length}});
}
