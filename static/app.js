'use strict';
const fmt = (n, digits=1) => n === null || n === undefined ? '—' : new Intl.NumberFormat('de-AT',{maximumFractionDigits:digits}).format(n);
const datefmt = value => new Date(value).toLocaleString('de-AT',{timeZone:'Europe/Vienna',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
const setText = (id,text) => { const el=document.getElementById(id); if(el) el.textContent=text; };
const PALETTE=['#4d5256','#f8ba02','#e0892f','#5f8296','#b3412e','#6f8a5f','#7b6fb0','#2f7d6b'];
if(document.getElementById('dashboard')) {
 const dashboard=document.getElementById('dashboard');
 const exportUrl=document.getElementById('csv-export').getAttribute('href');
 let hours=Number(dashboard.dataset.defaultHours)||24, custom=null, chartData=null, serial=0, latestPoints={};
 const usable = p => p && !p.stale && p.latest.quality==='ok';
 let preferenceTimer=null, preferenceQueue=Promise.resolve();
 function savePreferences(){
  clearTimeout(preferenceTimer);
  setText('chart-preferences-status','Auswahl wird gespeichert …');
  preferenceTimer=setTimeout(()=>{
   const body=JSON.stringify({points:selectedKeys(),hours});
   preferenceQueue=preferenceQueue.then(async()=>{
    try{
     const token=document.querySelector('#chart-preferences input[name="csrfmiddlewaretoken"]').value;
     const response=await fetch(dashboard.dataset.preferencesUrl,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':token},body});
     if(!response.ok||response.redirected)throw Error();
     setText('chart-preferences-status','Auswahl für deinen Benutzer gespeichert.');
    }catch(e){setText('chart-preferences-status','Auswahl konnte nicht gespeichert werden. Bitte erneut auswählen.');}
   });
  },300);
 }
 function selectedKeys(){return [...document.querySelectorAll('#series-menu input:checked')].map(el=>el.value);}
 function refreshSummary(){setText('chart-summary',selectedKeys().map(k=>{const el=document.querySelector('#series-menu input[value="'+k+'"]');return el?el.dataset.label:'';}).filter(Boolean).join(', ')||'Nichts ausgewählt');}
 function query(){const q=new URLSearchParams();q.set('point',selectedKeys().join(',')||'power');if(custom){q.set('start',custom.start);q.set('end',custom.end);}else q.set('hours',hours);return q;}
 function selectedLive(){
  const target=document.getElementById('selected-live-values'); if(!target)return;
  target.replaceChildren();
  selectedKeys().forEach(key=>{
   const p=latestPoints[key], card=document.createElement('div'), label=document.createElement('span'), value=document.createElement('strong');
   label.textContent=p?.label||document.querySelector('#series-menu input[value="'+key+'"]')?.dataset.label||key;
   const v=p?.latest,derived=document.querySelector('#series-menu input[value="'+key+'"]')?.dataset.derived==='1';
   value.textContent=derived?'Berechnung im Diagramm':usable(p)?(v.unit?fmt(v.value,2)+' '+v.unit:(v.text||fmt(v.value))):'Kein aktueller Wert';
   card.append(label,value);target.append(card);
  });
 }
 async function live(){
  try{
   const response=await fetch(dashboard.dataset.liveUrl); if(!response.ok || response.redirected) throw Error('Anmeldung erforderlich');
   const data=await response.json(), map=Object.fromEntries(data.points.map(p=>[p.key,p]));
   latestPoints=map;selectedLive();
   const sensors=document.getElementById('mqtt-sensor-values');
   if(sensors){
    sensors.replaceChildren();
    data.points.filter(p=>p.key.startsWith('esp-')).forEach(p=>{
     const menu=document.getElementById('series-menu');
     if(![...menu.querySelectorAll('input')].some(el=>el.value===p.key)){
      const option=document.createElement('label'),checkbox=document.createElement('input'),text=document.createElement('span');
      option.className='check series-opt';checkbox.type='checkbox';checkbox.value=p.key;checkbox.dataset.label=p.label;checkbox.dataset.unit=p.latest.unit;text.textContent=p.label;
      checkbox.addEventListener('change',()=>{refreshSummary();selectedLive();history();savePreferences();});option.append(checkbox,text);menu.append(option);
     }
     const card=document.createElement('article');card.className='metric';
     const label=document.createElement('span'),value=document.createElement('strong'),stamp=document.createElement('small'),button=document.createElement('button');
     label.textContent=p.label;value.textContent=usable(p)?fmt(p.latest.value,2)+' '+p.latest.unit:'Kein aktueller Wert';
     stamp.textContent=p.section+(p.at?' · '+datefmt(p.at):'');
     button.type='button';button.className='text-button';button.textContent='Verlauf anzeigen';
     button.addEventListener('click',()=>{document.querySelectorAll('#series-menu input').forEach(el=>el.checked=el.value===p.key);refreshSummary();selectedLive();history();savePreferences();document.getElementById('chart').scrollIntoView({behavior:'smooth',block:'center'});});
     card.append(label,value,stamp,button);sensors.append(card);
    });
    document.getElementById('mqtt-sensors').hidden=!sensors.children.length;
   }
   const connected=data.logger.ok && !data.worker.stale;
   setText('connection',connected?'Live verbunden':'Verbindung prüfen');
   document.getElementById('connection').classList.toggle('warning',!connected);
   setText('power',usable(map.power)?fmt(map.power.latest.value):'—');
   setText('boiler-state',usable(map.status)?map.status.latest.text:'Status unbekannt');
   const load=usable(map.power)?map.power.latest.value/50*100:null;
   document.getElementById('load-bar').style.width=Math.max(0,Math.min(100,load||0))+'%';
   setText('load-label',load===null?'Kein aktueller Leistungswert':fmt(load)+' % der Nennleistung');
   setText('last-reading',map.power?.at?'Letzter Abruf '+datefmt(map.power.at):'Noch keine Messwerte');
   document.querySelectorAll('[data-point]').forEach(el=>{const p=map[el.dataset.point];el.textContent=usable(p)?fmt(p.latest.value):'—';});
   const list=document.getElementById('live-list'); list.replaceChildren();
   ['buffer-top','buffer-bottom','return','flue','hours','starts'].forEach(key=>{
    const p=map[key]; if(!p)return; const row=document.createElement('div'), name=document.createElement('span'), val=document.createElement('strong');
    name.textContent=p.label;val.textContent=usable(p)?(key==='hours'?p.latest.text:fmt(p.latest.value)+' '+p.latest.unit):'Nicht aktuell';row.append(name,val);list.append(row);
   });
   const errors=document.getElementById('errors');errors.replaceChildren();
   setText('error-count',data.errors.ok?String(data.errors.items.length):'UNBEKANNT');
   if(!data.errors.ok || data.worker.stale){const p=document.createElement('p');p.textContent='Meldungsstatus nicht aktuell verfügbar.';p.className='muted';errors.append(p);}
   else if(!data.errors.items.length){const box=document.createElement('div');box.className='empty-success';const icon=document.createElement('b');icon.textContent='✓';const text=document.createElement('span');const heading=document.createElement('strong');heading.textContent='Keine aktiven Störungen';text.append(heading,document.createTextNode('Die Anlage meldet aktuell keine Fehler.'));box.append(icon,text);errors.append(box);}
   else data.errors.items.forEach(item=>{const row=document.createElement('p');row.className='notice warning';row.textContent=item.section+': '+item.message;errors.append(row);});
  }catch(e){setText('connection','Verbindung unterbrochen');document.getElementById('connection').classList.add('warning');document.querySelectorAll('[data-point]').forEach(el=>el.textContent='—');document.querySelectorAll('#mqtt-sensor-values strong').forEach(el=>el.textContent='Kein aktueller Wert');setText('power','—');setText('boiler-state','Status unbekannt');}
 }
 async function history(){
  if(!selectedKeys().length){++serial;chartData=null;document.getElementById('chart').replaceChildren();document.getElementById('selected-live-values').replaceChildren();document.getElementById('chart-legend').hidden=true;setText('chart-message','Bitte mindestens eine Messgröße auswählen.');return;}
  const mine=++serial,q=query();document.getElementById('csv-export').href=exportUrl+'?'+q;
  try{const response=await fetch(dashboard.dataset.historyUrl+'?'+q);if(!response.ok)throw Error('Zeitraum ungültig oder Sitzung abgelaufen');const data=await response.json();if(mine!==serial)return;chartData=data;draw();
   setText('stat-utilization',fmt(data.stats.utilization)+' %');setText('stat-energy',fmt(data.stats.energy,2)+' kWh');setText('stat-runtime',fmt(data.stats.runtime,2)+' h');setText('stat-starts',fmt(data.stats.starts,0));
   setText('runtime-share',fmt(data.stats.runtime_share)+' % des erfassten Zeitraums');
   setText('coverage','Datenabdeckung: '+fmt(data.stats.coverage)+' %. Auswertung nur für lückenlos erfasste Abschnitte; Wärmemenge ist eine Näherung aus den ETA-Leistungswerten.'+(data.stats.counter_reset?' Zählerwechsel erkannt; Zündungsdifferenz nicht berechnet.':''));
  }catch(e){setText('chart-message',e.message);}
 }
 function draw(){
  if(!chartData)return;
  const target=document.getElementById('chart');target.replaceChildren();const ns='http://www.w3.org/2000/svg';const series=chartData.series||[];
  const active=series.filter(s=>s.values.some(p=>p.value!==null));
  if(!active.length){const el=document.createElement('div');el.className='chart-empty';const title=document.createElement('strong');title.textContent='Die ersten Messwerte kommen gleich.';el.append(title,document.createTextNode('Für diesen Zeitraum sind noch keine Messwerte vorhanden.'));target.append(el);setText('chart-message','Die Aufzeichnung beginnt mit dem Start des Loggers.');return;}
  const w=Math.max(300,target.clientWidth),h=target.clientHeight,left=44,right=w-16,top=18,bottom=h-32;
  let max=Math.max(1,...active.flatMap(s=>s.values.map(p=>p.value===null?Number.NEGATIVE_INFINITY:p.value)));
  let min=Math.min(0,...active.flatMap(s=>s.values.map(p=>p.value===null?Number.POSITIVE_INFINITY:p.value)));
  if(max===min)max=min+1;max+=(max-min)*.12;
  const start=Date.parse(chartData.start),end=Date.parse(chartData.end);const x=t=>left+(Date.parse(t)-start)/(end-start)*(right-left),y=v=>bottom-(v-min)/(max-min)*(bottom-top);
  const svg=document.createElementNS(ns,'svg');svg.setAttribute('viewBox',`0 0 ${w} ${h}`);target.append(svg);
  function node(tag,attrs,text){const n=document.createElementNS(ns,tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));if(text!==undefined)n.textContent=text;svg.append(n);return n;}
  for(let i=0;i<5;i++){const yy=top+(bottom-top)*i/4;node('line',{x1:left,y1:yy,x2:right,y2:yy,stroke:'#e4e5e1','stroke-dasharray':'3 4'});node('text',{x:left-8,y:yy+3,'text-anchor':'end'},fmt(max-(max-min)*i/4));}
  for(let i=0;i<5;i++){const t=new Date(start+(end-start)*i/4);const text=t.toLocaleString('de-AT',{timeZone:'Europe/Vienna',...(end-start>2*864e5?{day:'2-digit',month:'2-digit'}:{hour:'2-digit',minute:'2-digit'})});node('text',{x:left+(right-left)*i/4,y:h-8,'text-anchor':i===0?'start':i===4?'end':'middle'},text);}
  series.forEach((s,si)=>{const stroke=PALETTE[si%PALETTE.length];let segment=[];function flush(){if(!segment.length)return;const d=segment.map((p,i)=>(i?'L':'M')+x(p.at).toFixed(1)+','+y(p.value).toFixed(1)).join(' ');if(segment.length>1){node('path',{d,fill:'none',stroke,'stroke-width':'2','stroke-linejoin':'round'});}else node('circle',{cx:x(segment[0].at),cy:y(segment[0].value),r:3,fill:stroke});segment=[];}s.values.forEach(p=>{if(p.value===null){if(p.gap)flush();}else{if(segment.length&&Date.parse(p.at)-Date.parse(segment[segment.length-1].at)>Math.max((chartData.sample_interval||30)*2.5*1000,(end-start)/600*1.5))flush();segment.push(p);}});flush();});
  const legend=document.getElementById('chart-legend');legend.replaceChildren();
  series.forEach((s,si)=>{const item=document.createElement('span');item.className='legend-item';const dot=document.createElement('i');dot.className='legend-dot';dot.style.background=PALETTE[si%PALETTE.length];const txt=document.createElement('span');txt.textContent=s.label+(s.unit?' · '+s.unit:'');item.append(dot,txt);legend.append(item);});legend.hidden=false;
  const tooltip=document.createElement('div');tooltip.className='chart-tooltip';tooltip.hidden=true;target.append(tooltip);
  svg.addEventListener('pointermove',event=>{const pos=event.clientX-svg.getBoundingClientRect().left;const anchor=active[0].values;let idx=anchor.reduce((a,b,i)=>Math.abs(x(b.at)-pos)<Math.abs(x(anchor[a].at)-pos)?i:a,0);const at=anchor[idx].at;const lines=series.map((s,i)=>{const v=s.values[idx]?.value;return v===null||v===undefined?null:s.label+' '+fmt(v,2)+' '+s.unit;}).filter(Boolean);tooltip.textContent=datefmt(at)+(lines.length?' · '+lines.join(' · '):'');tooltip.hidden=false;});svg.addEventListener('pointerleave',()=>tooltip.hidden=true);
  setText('chart-message',active.map(s=>s.label).join(' + ')+' · '+datefmt(chartData.start)+' bis '+datefmt(chartData.end)+' · Zeitraster mit Mittelwerten, maximal 600 Punkte je Reihe, Lücken bleiben sichtbar');
 }
 document.querySelectorAll('.range').forEach(btn=>btn.addEventListener('click',()=>{hours=Number(btn.dataset.hours);custom=null;document.querySelectorAll('.range').forEach(b=>b.classList.toggle('selected',b===btn));history();savePreferences();}));
 document.getElementById('series-menu').querySelectorAll('input').forEach(chk=>chk.addEventListener('change',()=>{refreshSummary();selectedLive();history();savePreferences();}));
 document.getElementById('apply-range').addEventListener('click',()=>{const a=document.getElementById('range-start').value,b=document.getElementById('range-end').value;if(!a||!b){setText('chart-message','Bitte Beginn und Ende auswählen.');return;}custom={start:new Date(a).toISOString(),end:new Date(b).toISOString()};document.querySelectorAll('.range').forEach(btn=>btn.classList.remove('selected'));history();});
 window.addEventListener('resize',draw);refreshSummary();live();history();setInterval(live,15000);setInterval(history,60000);
}
if(document.getElementById('schedule')) {
 // Zeitschaltuhr: Balken, Prüfung und Änderungszähler laufen im Browser; der Server prüft alles erneut.
 const form=document.getElementById('schedule-form'), days=[...form.querySelectorAll('.tl-day')];
 const ORDER=['mon','tue','wed','thu','fri','sat','sun'], SHORT={mon:'Mo',tue:'Di',wed:'Mi',thu:'Do',fri:'Fr',sat:'Sa',sun:'So'};
 const toMin=v=>{const m=/^(\d{2}):(\d{2})$/.exec(v||'');return m?Number(m[1])*60+Number(m[2]):null;};
 const hhmm=m=>String(Math.floor(m/60)).padStart(2,'0')+':'+String(m%60).padStart(2,'0');
 const shown=m=>m===1440?'24:00':hhmm(m);
 const field=(slot,suffix)=>slot.querySelector('[name$="-'+suffix+'"]');
 const slotsOf=day=>[...day.querySelectorAll('[data-slot]')];
 const tempOf=day=>day.querySelector('.tl-outside input[type=number]');
 function original(slot){if(!slot.dataset.orig)return undefined;const [b,e]=slot.dataset.orig.split('-').map(Number);return e>b?{begin:b*15,end:e*15}:null;}
 function current(slot){
  // {begin,end} in Minuten; null = kein Zeitfenster; undefined = Zustand unbekannt
  const on=field(slot,'on');
  if(!on||on.disabled)return original(slot);
  if(!on.checked)return null;
  let e=toMin(field(slot,'end').value);if(e===0)e=1440;
  return {begin:toMin(field(slot,'begin').value),end:e};
 }
 function viennaNow(){
  const p=Object.fromEntries(new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Vienna',weekday:'short',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(new Date()).map(x=>[x.type,x.value]));
  return {day:{Mon:'mon',Tue:'tue',Wed:'wed',Thu:'thu',Fri:'fri',Sat:'sat',Sun:'sun'}[p.weekday],min:Number(p.hour)*60+Number(p.minute)};
 }
 function segment(bar,cls,from,to,title){const seg=document.createElement('span');seg.className=cls;seg.style.left=(from/14.4)+'%';seg.style.width=((to-from)/14.4)+'%';seg.title=title;bar.append(seg);}
 function render(day){
  const bar=day.querySelector('.tl-bar'), text=day.querySelector('.tl-text'), error=day.querySelector('.tl-error'), problems=[], windows=[];
  bar.replaceChildren();text.replaceChildren();
  slotsOf(day).forEach((slot,i)=>{
   const w=current(slot), on=field(slot,'on');
   slot.classList.toggle('off',!!on&&!on.checked);slot.classList.remove('invalid');
   if(w===undefined){segment(bar,'tl-seg unknown',0,1440,'Zustand unbekannt');const s=document.createElement('span');s.textContent='Fenster '+(i+1)+' unbekannt';text.append(s);return;}
   if(!w)return;
   if(w.begin===null||w.end===null||w.begin%15||w.end%15){problems.push('Zeitfenster '+(i+1)+': Uhrzeiten nur im Viertelstundenraster (z. B. 06:15).');slot.classList.add('invalid');return;}
   if(w.end<=w.begin){problems.push('Zeitfenster '+(i+1)+': Ende muss nach dem Beginn liegen.');slot.classList.add('invalid');return;}
   windows.push({...w,n:i+1,slot});
  });
  windows.sort((a,b)=>a.begin-b.begin).forEach((w,i,all)=>{
   const prev=all[i-1];
   if(prev&&w.begin<prev.end){problems.push('Zeitfenster '+prev.n+' und '+w.n+' überschneiden sich.');w.slot.classList.add('invalid');prev.slot.classList.add('invalid');}
   segment(bar,'tl-seg',w.begin,w.end,hhmm(w.begin)+'–'+shown(w.end));
   const s=document.createElement('span');s.textContent=hhmm(w.begin)+'–'+shown(w.end);text.append(s);
  });
  const now=viennaNow(), today=day.dataset.day===now.day;
  day.classList.toggle('today',today);
  if(today){const mark=document.createElement('span');mark.className='tl-now';mark.style.left=(now.min/14.4)+'%';mark.title='Jetzt '+hhmm(now.min);bar.append(mark);}
  error.textContent=problems.join(' ');error.hidden=!problems.length;
  return problems.length;
 }
 function changed(day){
  let n=0;
  slotsOf(day).forEach(slot=>{const on=field(slot,'on');if(!on||on.disabled)return;const b=field(slot,'begin'),e=field(slot,'end');if(on.checked!==on.defaultChecked||(on.checked&&(b.value!==b.defaultValue||e.value!==e.defaultValue)))n++;});
  const t=tempOf(day);if(t&&!t.disabled&&Number(t.value)!==Number(t.defaultValue))n++;
  return n;
 }
 function status(){
  // Bezieht sich auf die gespeicherten ETA-Werte, nicht auf ungespeicherte Eingaben.
  const el=document.getElementById('schedule-now'), now=viennaNow(), start=ORDER.indexOf(now.day);
  const saved=Object.fromEntries(days.map(d=>[d.dataset.day,slotsOf(d).map(original)]));
  if(start<0||days.length<7||Object.values(saved).flat().some(o=>o===undefined))return;
  const list=d=>saved[d].filter(Boolean).sort((a,b)=>a.begin-b.begin);
  const active=list(now.day).find(o=>o.begin<=now.min&&now.min<o.end);
  el.classList.toggle('on',!!active);el.classList.toggle('off',!active);
  if(active){el.textContent='Laut Zeitschaltuhr jetzt Heizzeit, bis '+shown(active.end)+'.';return;}
  for(let k=0;k<8;k++){const d=ORDER[(start+k)%7];const next=list(d).find(o=>k>0||o.begin>now.min);if(next){el.textContent='Laut Zeitschaltuhr jetzt abgesenkt. Nächste Heizzeit '+(k===0?'heute':k===1?'morgen':SHORT[d])+' um '+hhmm(next.begin)+'.';return;}}
  el.textContent='Keine Heizzeiten eingestellt.';
 }
 function refresh(){
  let total=0,invalid=0;
  days.forEach(d=>{invalid+=render(d);const n=changed(d);total+=n;d.classList.toggle('changed',n>0);});
  const label=document.getElementById('schedule-changes'), save=document.getElementById('schedule-save');
  if(label){label.textContent=invalid?'Bitte Eingaben prüfen':total?total+(total===1?' Änderung':' Änderungen')+' noch nicht gespeichert':'Keine Änderungen';label.classList.toggle('dirty',total>0||invalid>0);}
  if(save)save.disabled=!total||invalid>0;
  status();
  return total;
 }
 function setSlots(day,windows){
  slotsOf(day).forEach((slot,i)=>{const on=field(slot,'on');if(!on||on.disabled)return;const w=windows[i];on.checked=!!w;if(w){field(slot,'begin').value=hhmm(w.begin);field(slot,'end').value=hhmm(w.end%1440);}});
 }
 days.forEach(day=>{
  day.querySelectorAll('[data-js]').forEach(el=>el.hidden=false);
  day.querySelectorAll('[data-preset]').forEach(btn=>btn.addEventListener('click',()=>{
   setSlots(day,btn.dataset.preset?btn.dataset.preset.split(',').map(p=>{const [b,e]=p.split('-').map(Number);return {begin:b*15,end:e*15};}):[]);refresh();
  }));
  slotsOf(day).forEach(slot=>{const on=field(slot,'on');if(on)on.addEventListener('change',()=>{
   // Leeres Fenster sinnvoll vorbelegen: nach dem letzten Fenster des Tages, sonst 06:00-08:00.
   const b=field(slot,'begin'),e=field(slot,'end');
   if(!on.checked||b.value||e.value)return;
   const last=slotsOf(day).filter(s=>s!==slot).map(current).filter(Boolean).reduce((m,w)=>Math.max(m,w.end||0),0);
   const s=last&&last<=1320?Math.ceil((last+60)/15)*15:360;
   b.value=hhmm(Math.min(s,1380));e.value=hhmm(Math.min(s+120,1440)%1440);
  });});
  const copy=day.querySelector('[data-copy]');
  if(copy)copy.addEventListener('change',()=>{
   const targets=copy.value.split(',').filter(d=>d&&d!==day.dataset.day);copy.value='';
   const windows=slotsOf(day).map(current);
   if(windows.some(w=>w===undefined))return;
   const temp=tempOf(day);
   targets.forEach(key=>{const target=days.find(d=>d.dataset.day===key);if(!target)return;setSlots(target,windows);const t=tempOf(target);if(temp&&t&&!t.disabled)t.value=temp.value;});
   refresh();
  });
 });
 form.addEventListener('input',refresh);form.addEventListener('change',refresh);
 form.addEventListener('reset',()=>setTimeout(refresh));
 form.addEventListener('submit',event=>{const save=document.getElementById('schedule-save');if(!save||save.disabled){event.preventDefault();return;}form.dataset.sending='1';setTimeout(()=>{save.disabled=true;save.textContent='Wird gesendet …';});});
 window.addEventListener('beforeunload',event=>{if(!form.dataset.sending&&days.some(d=>changed(d)>0)){event.preventDefault();event.returnValue='';}});
 refresh();setInterval(()=>{days.forEach(render);status();},60000);
}
document.querySelectorAll('select[data-autosubmit]').forEach(sel=>{sel.addEventListener('change',()=>sel.form.submit());sel.form.querySelectorAll('[data-js-hide]').forEach(b=>b.hidden=true);});
