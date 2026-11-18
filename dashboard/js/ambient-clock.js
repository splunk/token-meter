// Ambient motion (atmosphere, foil, ring, title shimmer, status pulses, and both header shaders) runs on
// one shared clock. Every frame the browser paints re-composites the whole page, whatever moved, so
// stepping all of it together at AMBIENT_FPS paints that many frames a second instead of 60 or more.
// The clock seeks the CSS animations it adopts; while it is stopped every frame holds where it is.
const AMBIENT_FPS=20,AMBIENT_FRAME_MS=1000/AMBIENT_FPS,AMBIENT_EARLY_MS=8,AMBIENT_MAX_STEP_MS=250;
const AMBIENT_ANIMATIONS=new Set(['atmosphereDrift','foilDrift','ringSpin','titleShimmer','sessionFlowStatusPulse','cacheRingPulse']);
const ambientClockAnimations=new Map();
let ambientClockTime=0,ambientClockLast=0,ambientClockTimer=0,ambientClockFrame=0;
const ambientClockRunning=()=>!document.hidden&&!ambientMotionStopped()&&!pageSignalReducedMotionQuery.matches;
function adoptAmbientAnimation(animation){
 if(ambientClockAnimations.has(animation)||!AMBIENT_ANIMATIONS.has(animation.animationName))return;
 ambientClockAnimations.set(animation,(Number(animation.currentTime)||0)-ambientClockTime);
 animation.pause();
}
function ambientClockTick(){
 ambientClockFrame=0;
 if(!ambientClockRunning()){ambientClockLast=0;return;}
 // A display-frame timestamp can trail the real time by a frame, so pace from performance.now().
 const now=performance.now();
 if(ambientClockLast&&now-ambientClockLast<AMBIENT_FRAME_MS-AMBIENT_EARLY_MS-4){ambientClockFrame=requestAnimationFrame(ambientClockTick);return;}
 const dt=ambientClockLast?Math.min(Math.max(0,now-ambientClockLast),AMBIENT_MAX_STEP_MS):0;
 ambientClockLast=now;ambientClockTime+=dt;
 for(const [animation,offset] of ambientClockAnimations){
  if(animation.playState==='idle'){ambientClockAnimations.delete(animation);continue;}
  animation.currentTime=offset+ambientClockTime;
 }
 activePageHeroHandle?.tick(dt);activePageBorderHandle?.tick(dt);
 scheduleAmbientClock(now+AMBIENT_FRAME_MS);
}
// Wake half a display frame before the step is due, then paint on the next display frame.
function scheduleAmbientClock(due=performance.now()){
 if(ambientClockTimer||ambientClockFrame||!ambientClockRunning())return;
 ambientClockTimer=setTimeout(()=>{ambientClockTimer=0;ambientClockFrame=requestAnimationFrame(ambientClockTick);},Math.max(0,due-performance.now()-AMBIENT_EARLY_MS));
}
function syncAmbientClock(){
 if(ambientClockRunning()){
  document.getAnimations().forEach(adoptAmbientAnimation);
  scheduleAmbientClock();
  return;
 }
 clearTimeout(ambientClockTimer);ambientClockTimer=0;
 cancelAnimationFrame(ambientClockFrame);ambientClockFrame=0;ambientClockLast=0;
}
document.addEventListener('animationstart',event=>{
 if(!AMBIENT_ANIMATIONS.has(event.animationName))return;
 event.target.getAnimations({subtree:true}).forEach(adoptAmbientAnimation);
});
document.addEventListener('visibilitychange',syncAmbientClock);
pageSignalReducedMotionQuery.addEventListener?.('change',syncAmbientClock);
// end-ambient-clock
