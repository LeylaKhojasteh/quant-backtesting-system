// Seeded visualization data for the explicit `?fixture=demo` path and tests.
// The REAL run path must never import market/execution data from this module.
import { fmt, time } from './format';
import type { Candle, Trade } from './types';

export { fmt, time };
export type { Candle, Trade };

export const base=Date.UTC(2025,0,31);
const records=[[0,2.44,2799.84,-5.16,'SL'],[3,5.1,2791.02,-5.02,'SL'],[9,11.62,2784.91,-5.02,'SL'],[12,14.08,2776.32,-5.04,'SL'],[15,16.72,2781.20,10.04,'TP'],[18,20.05,2797.66,-5.18,'SL'],[21,22,2797.85,-0.44,'EOD']] as const;
export const trades:Trade[]=records.map((r,i)=>{const direction=i%2===0?'LONG':'SHORT',s=direction==='LONG'?1:-1;return{id:i+1,direction,signal:base+r[0]*3600000,signalAvailable:base+(r[0]*3600000)+900000,entryTime:base+(r[0]+.25)*3600000+124,exitTime:base+r[1]*3600000,entry:r[2],exit:r[2]+r[3]*s,pnl:r[3],reason:r[4],spreadIn:.76-i*.012,spreadOut:.72+i*.006,sl:r[2]-5*s,tp:r[2]+10*s}});
export const candles:Candle[]=Array.from({length:88},(_,i)=>{const o=2795+12*Math.sin(i/10)+6*Math.sin(i/3)-i*.08,c=o+Math.sin(i*2.71)*2.8;return{t:base+i*900000,o,c,h:Math.max(o,c)+1.2+Math.abs(Math.cos(i))*1.2,l:Math.min(o,c)-1.3-Math.abs(Math.sin(i))}});
export const cumulative=[-5.16,-10.18,-15.20,-20.24,-10.20,-15.38,-15.82];
export function tradePath(t:Trade){const askSide=t.direction==='LONG';return Array.from({length:81},(_,i)=>{const at=t.entryTime+(t.exitTime-t.entryTime)*i/80,observed=t.entry+(t.exit-t.entry)*i/80+(i===0||i===80?0:Math.sin(i*.8)*.05),spread=t.spreadIn+(t.spreadOut-t.spreadIn)*i/80;return{t:at,bid:i===80?(askSide?t.exit:t.exit-spread):(askSide?observed-spread:observed),ask:i===80?(askSide?t.exit+spread:t.exit):(askSide?observed:observed+spread)}})}
/** Fixture-only synthetic ticks (±window seconds around entry/exit). */
export function ticks(t:Trade,kind:'entry'|'exit',window:number){const anchor=kind==='entry'?t.entryTime:t.exitTime,price=kind==='entry'?t.entry:t.exit,spread=kind==='entry'?t.spreadIn:t.spreadOut;return Array.from({length:window*2+1},(_,i)=>{const offset=i-window,s=spread+(offset===0?0:Math.sin(offset*1.3)*.035),observed=price+(offset===0?0:Math.sin(offset*.6)*.14+offset*.006),askSide=kind==='entry'?t.direction==='LONG':t.direction==='SHORT';return{t:anchor+offset*1000,bid:askSide?observed-s:observed,ask:askSide?observed:observed+s,spread:s,event:offset===0?kind.toUpperCase():'—'}})}
export { strategyDefinitions } from './strategy-definitions';

