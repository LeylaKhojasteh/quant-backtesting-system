'use client';
import {useEffect,useRef,useState,ReactNode} from 'react';
import {Maximize2,Minimize2} from 'lucide-react';

// Expand the existing panel in place so chart zoom, overlays and selection survive.
// The document enters native fullscreen, keeping portaled Select menus usable.
// Embedded previews that deny native fullscreen use the same viewport layout.
export function FullscreenPanel({title,icon,actions,children,className=''}:{title:string;icon?:ReactNode;actions?:ReactNode;children:ReactNode;className?:string}){
 const [expanded,setExpanded]=useState(false);
 const ownsNative=useRef(false);
 const toggleRef=useRef<HTMLButtonElement>(null);
 const close=async()=>{
  setExpanded(false);
  if(ownsNative.current&&document.fullscreenElement){ownsNative.current=false;try{await document.exitFullscreen()}catch{}}
  toggleRef.current?.focus();
 };
 useEffect(()=>{
  if(!expanded)return;
  const previous=document.body.style.overflow;
  document.body.style.overflow='hidden';
  const escape=(e:KeyboardEvent)=>{if(e.key==='Escape'){void close()}};
  const change=()=>{if(ownsNative.current&&!document.fullscreenElement){ownsNative.current=false;setExpanded(false);toggleRef.current?.focus()}};
  document.addEventListener('keydown',escape);
  document.addEventListener('fullscreenchange',change);
  return()=>{
   document.body.style.overflow=previous;
   document.removeEventListener('keydown',escape);
   document.removeEventListener('fullscreenchange',change);
   if(ownsNative.current&&document.fullscreenElement){ownsNative.current=false;void document.exitFullscreen().catch(()=>{})}
  };
 },[expanded]);
 const toggle=async()=>{
  if(expanded){await close();return}
  setExpanded(true);
  if(document.fullscreenEnabled&&!document.fullscreenElement){
   try{await document.documentElement.requestFullscreen();ownsNative.current=true}catch{/* Viewport mode remains interactive in embedded browsers. */}
  }
 };
 return <section className={`panel ${className} ${expanded?'is-fullscreen':''}`} data-fullscreen={expanded} aria-label={title}>
  <div className="panel-head"><h2>{icon}{title}{expanded&&<span className="fullscreen-hint">ESC to exit</span>}</h2><div className="actions">{actions}<button ref={toggleRef} type="button" className="fullscreen-toggle" aria-label={`${expanded?'Exit':'Enter'} fullscreen ${title}`} aria-pressed={expanded} title={expanded?'Exit fullscreen (Esc)':'Fullscreen'} onClick={()=>void toggle()}>{expanded?<Minimize2 size={14}/>:<Maximize2 size={14}/>}</button></div></div>
  {children}
 </section>;
}
