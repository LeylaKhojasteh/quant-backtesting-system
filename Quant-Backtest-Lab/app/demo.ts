// Seeded visualization data only. Replace this adapter with Python engine results.
export type Trade={id:number;direction:'LONG'|'SHORT';signal:number;entryTime:number;exitTime:number;entry:number;exit:number;pnl:number;reason:'SL'|'TP'|'EOD';spreadIn:number;spreadOut:number;sl:number;tp:number};
export const base=Date.UTC(2025,0,31);
export const fmt=(n:number)=>n.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
export const time=(n:number,full=false)=>new Date(n).toISOString().slice(full?0:11,23).replace('T',' ');
const records=[[0,2.44,2799.84,-5.16,'SL'],[3,5.1,2791.02,-5.02,'SL'],[9,11.62,2784.91,-5.02,'SL'],[12,14.08,2776.32,-5.04,'SL'],[15,16.72,2781.20,10.04,'TP'],[18,20.05,2797.66,-5.18,'SL'],[21,22,2797.85,-0.44,'EOD']] as const;
export const trades:Trade[]=records.map((r,i)=>{const direction=i%2===0?'LONG':'SHORT',s=direction==='LONG'?1:-1;return{id:i+1,direction,signal:base+r[0]*3600000,entryTime:base+(r[0]+.25)*3600000+124,exitTime:base+r[1]*3600000,entry:r[2],exit:r[2]+r[3]*s,pnl:r[3],reason:r[4],spreadIn:.76-i*.012,spreadOut:.72+i*.006,sl:r[2]-5*s,tp:r[2]+10*s}});
export type Candle={t:number;o:number;h:number;l:number;c:number};
export const candles:Candle[]=Array.from({length:88},(_,i)=>{const o=2795+12*Math.sin(i/10)+6*Math.sin(i/3)-i*.08,c=o+Math.sin(i*2.71)*2.8;return{t:base+i*900000,o,c,h:Math.max(o,c)+1.2+Math.abs(Math.cos(i))*1.2,l:Math.min(o,c)-1.3-Math.abs(Math.sin(i))}});
export const cumulative=[-5.16,-10.18,-15.20,-20.24,-10.20,-15.38,-15.82];
export function tradePath(t:Trade){const askSide=t.direction==='LONG';return Array.from({length:81},(_,i)=>{const at=t.entryTime+(t.exitTime-t.entryTime)*i/80,observed=t.entry+(t.exit-t.entry)*i/80+(i===0||i===80?0:Math.sin(i*.8)*.05),spread=t.spreadIn+(t.spreadOut-t.spreadIn)*i/80;return{t:at,bid:i===80?(askSide?t.exit:t.exit-spread):(askSide?observed-spread:observed),ask:i===80?(askSide?t.exit+spread:t.exit):(askSide?observed:observed+spread)}})}
export function ticks(t:Trade,kind:'entry'|'exit',window:number){const anchor=kind==='entry'?t.entryTime:t.exitTime,price=kind==='entry'?t.entry:t.exit,spread=kind==='entry'?t.spreadIn:t.spreadOut;return Array.from({length:window*2+1},(_,i)=>{const offset=i-window,s=spread+(offset===0?0:Math.sin(offset*1.3)*.035),observed=price+(offset===0?0:Math.sin(offset*.6)*.14+offset*.006),askSide=kind==='entry'?t.direction==='LONG':t.direction==='SHORT';return{t:anchor+offset*1000,bid:askSide?observed-s:observed,ask:askSide?observed:observed+s,spread:s,event:offset===0?kind.toUpperCase():'—'}})}

export const strategyDefinitions = {
  'Candle Direction Test': {
    description: 'Bullish candles generate LONG signals; bearish candles generate SHORT signals.',
    inputs: [
      {key:'stop', label:'Stop Distance', type:'float', defaultValue:5, min:0.1, step:0.1},
      {key:'take', label:'Take Profit Distance', type:'float', defaultValue:10, min:0.1, step:0.1},
    ],
  },
};
export function validationChecks(t:Trade){
 const entryTick=ticks(t,'entry',1)[1],exitTick=ticks(t,'exit',1)[1],long=t.direction==='LONG';
 return [
  {label:'Entry time ≥ signal available',pass:t.entryTime>=t.signal+900000},
  {label:`${long?'Long':'Short'} entry = ${long?'ASK':'BID'}`,pass:Math.abs(t.entry-(long?entryTick.ask:entryTick.bid))<1e-8},
  {label:`${long?'Long':'Short'} exit = ${long?'BID':'ASK'}`,pass:Math.abs(t.exit-(long?exitTick.bid:exitTick.ask))<1e-8},
  {label:'Exit time ≥ Entry time',pass:t.exitTime>=t.entryTime},
  {label:t.reason==='EOD'?'End-of-data close rule':`${t.reason} trigger rule (${long?'BID':'ASK'})`,pass:t.reason==='EOD'?t.exitTime===base+22*3600000:t.reason==='SL'?(long?t.exit<=t.sl:t.exit>=t.sl):(long?t.exit>=t.tp:t.exit<=t.tp)},
  {label:'Exit price uses observed tick',pass:Math.abs(t.exit-(long?exitTick.bid:exitTick.ask))<1e-8},
 ];
}
