"use strict";
(() => {
  const $ = id => document.getElementById(id);
  const nf = new Intl.NumberFormat();
  let state = null, shownState = null, birdRequest = 0, colorRequest = 0;
  let batteryLoaded = 0, archiveLoaded = "";
  function el(tag, className, text) {
    const n = document.createElement(tag);
    if (className) n.className = className;
    if (text != null) n.textContent = String(text);
    return n;
  }
  function link(label, value) {
    try {
      const u = new URL(value, location.href);
      if (!["http:", "https:"].includes(u.protocol) || u.username || u.password) return el("span", "", label);
      const a = el("a", "", label); a.href = u.href;
      a.target = "_blank"; a.rel = "noopener noreferrer"; return a;
    } catch { return el("span", "", label); }
  }
  async function get(url) {
    const r = await fetch(url, {credentials:"same-origin", cache:"no-store"});
    if (!r.ok) throw new Error(r.status === 409 ? "The print changed. Please open the comparison again." : "This information is temporarily unavailable.");
    return r.json();
  }
  function dateLabel(value, time = false) {
    if (!value) return "Not recorded";
    const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(value) ? value + "T12:00:00" : value);
    if (Number.isNaN(d.getTime())) return String(value);
    const opts = {month:"short",day:"numeric",year:"numeric"};
    if (time) Object.assign(opts,{hour:"numeric",minute:"2-digit",timeZone:state?.stats?.timezone || "America/Denver"});
    return d.toLocaleString([], opts);
  }
  function openDialog(id) { const d = $(id); if (!d.open) d.showModal(); }
  for (const d of document.querySelectorAll(".field-dialog")) {
    d.querySelector("[data-close-dialog]").addEventListener("click", () => d.close());
    d.addEventListener("click", e => { if (e.target === d) { const r=d.getBoundingClientRect(); if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)d.close(); } });
  }
  function renderHotspots(s) {
    shownState = s;
    const layer = $("bird-hotspots"); layer.replaceChildren();
    const size = s.canvas_size || {width:s.display_settings?.orientation === "landscape" ? 1600:1200,height:s.display_settings?.orientation === "landscape" ? 1200:1600};
    for (const h of s.hotspots || []) {
      const b=el("button","bird-hotspot"); b.type="button";
      b.setAttribute("aria-label", "View details for " + h.common_name);
      Object.assign(b.style,{left:(100*h.x/size.width)+"%",top:(100*h.y/size.height)+"%",width:(100*h.width/size.width)+"%",height:(100*h.height/size.height)+"%"});
      b.addEventListener("click", e => {e.stopPropagation(); openBird(h.scientific_name,(s.display_stats || s.stats)?.date);}); layer.append(b);
    }
    $("bird-click-hint").hidden = !layer.children.length;
  }
  function birdAt(event) {
    const s=shownState; if(!s) return null;
    const r=$("picture").getBoundingClientRect();
    const size=s.canvas_size || {width:1200,height:1600};
    const x=(event.clientX-r.left)*size.width/r.width, y=(event.clientY-r.top)*size.height/r.height;
    return (s.hotspots || []).filter(h => {
      if (x<h.x || y<h.y || x>=h.x+h.width || y>=h.y+h.height) return false;
      const m=h.alpha_mask;
      if (!m?.rows) return true;
      const mx=Math.min(m.width-1,Math.floor((x-h.x)*m.width/h.width));
      const my=Math.min(m.height-1,Math.floor((y-h.y)*m.height/h.height));
      return m.rows[my]?.[mx] === "1";
    }).sort((a,b) => a.width*a.height-b.width*b.height)[0];
  }
  $("picture-paper").addEventListener("click", e => {const h=birdAt(e);if(h)openBird(h.scientific_name,(shownState.display_stats || shownState.stats)?.date);});
  $("picture-paper").addEventListener("pointermove", e => {const h=birdAt(e);$("picture-paper").style.cursor=h?"pointer":"";$("picture-paper").title=h?"View details for "+h.common_name:"";});
  window.addEventListener("federfunk:image", e => renderHotspots(e.detail));
  window.addEventListener("federfunk:bird", e => openBird(e.detail.scientific_name,e.detail.date));
  window.addEventListener("federfunk:state", e => {
    state=e.detail;
    $("local-preview-note").hidden = !state.preview_only;
    if (state.preview_only) {
      $("apply-button").firstChild.textContent="Apply to preview ";
      $("apply-help").textContent="Preview only. Display settings are unchanged.";
    }
    if ($("battery-details").open && Date.now()-batteryLoaded>60000) loadBattery();
    if ($("archive-details").open && archiveLoaded!==(state.last_success || state.revision)) loadArchive();
  });
  $("compare-colors").addEventListener("click", async () => {
    const request=++colorRequest; openDialog("color-dialog"); $("color-grid").replaceChildren();
    $("color-status").textContent="Loading color previews…";
    try {
      const data=await get("/api/color-previews"); if(request!==colorRequest)return;
      for(const p of data.profiles) {
        const card=el("article","color-card");
        const image=el("img");image.alt=p.label || p.name || p.id; image.src=p.url;
        image.addEventListener("error",()=>{$("color-status").textContent="A comparison could not be loaded. Close and reopen to try the latest print.";});
        card.append(image,el("h3","",p.label || p.name || p.id));
        if(p.id==="current")card.append(el("span","tag","Current default"));
        else card.append(el("span","tag","Preview only"));
        card.append(el("p","small muted",p.description || "Alternative color conversion."),link("Open at full size ↗",p.url));
        $("color-grid").append(card);
      }
      $("color-status").textContent="Preview only. Frame colors are unchanged.";
    } catch(e) {$("color-status").textContent=e.message;}
  });
  function credits(assets) {
    const list=el("ul","detail-credits");
    for(const a of assets || []) {
      const row=el("li");row.append(el("strong","",a.title || a.common_name || a.scientific_name));
      row.append(el("p","small muted",[a.creator || a.artist,a.date || a.year,a.rights || a.license].filter(Boolean).join(" · ")));
      if(a.attribution)row.append(el("p","small muted",a.attribution));
      const changes=a.changes || a.modifications;
      if(changes)row.append(el("p","small muted","Image changes: "+(Array.isArray(changes)?changes.join("; "):changes)));
      const links=el("div","detail-links");
      if(a.source_url)links.append(link("Original source ↗",a.source_url));
      if(a.rights_url)links.append(link("License ↗",a.rights_url));
      if(a.upstream_url)links.append(link("Prepared drawing ↗",a.upstream_url));
      row.append(links);list.append(row);
    }
    return list;
  }
  function fact(list,title,value) {const group=el("div");group.append(el("dt","",title),el("dd","",value ?? "—"));list.append(group);}
  async function openBird(species,date,source="current") {
    if(!species)return;
    const request=++birdRequest;openDialog("bird-dialog");$("bird-heading").textContent="Bird details";$("bird-scientific").textContent=species;
    $("bird-content").replaceChildren();$("bird-status").textContent="Loading bird details…";
    try {
      const data=await get("/api/birds?"+new URLSearchParams({species,date:date || state?.stats?.date || "",source}));
      if(request!==birdRequest)return;
      $("bird-heading").textContent=data.common_name || species;$("bird-scientific").textContent=data.scientific_name;
      $("bird-status").textContent=data.source_note || "Automated identifications from your BirdNET station.";
      const grid=el("div","bird-detail-grid");
      const selectedId=data.print_artwork_id;
      const artwork=data.artworks?.find(a=>a.id===selectedId) || data.artworks?.[0];
      if(artwork){const image=el("img","bird-portrait");image.alt=data.common_name;image.src="/artwork/"+encodeURIComponent(artwork.id)+".png";if(artwork.display_mirrored)image.style.transform="scaleX(-1)";grid.append(image);}
      const notes=el("div");const facts=el("dl","detail-facts");
      fact(facts,"Detections in this print",nf.format(data.daily?.print_count ?? data.daily?.count ?? 0));
      fact(facts,"Lifetime detections",data.lifetime ? nf.format(data.lifetime.count) : "—");
      fact(facts,"First heard",dateLabel(data.lifetime?.first_heard));fact(facts,"Most recently heard",dateLabel(data.lifetime?.last_heard,true));
      notes.append(facts,el("p","field-help","Counts are detections, not individual birds."));
      const links=el("div","detail-links");
      if(data.birdnet_url)links.append(link("Open in BirdNET ↗",data.birdnet_url));
      links.append(link("Species reference ↗","https://en.wikipedia.org/wiki/"+encodeURIComponent(species.replaceAll(" ","_"))));
      notes.append(links);grid.append(notes);$("bird-content").append(grid);
      const recordings=el("section","detail-section");recordings.append(el("h3","","Recordings"),el("p","field-help","Listen or view a spectrogram in BirdNET."));
      if(!data.detections?.length)recordings.append(el("p","small muted","No individual detections are available for this date."));
      for(const d of data.detections || []) {
        const row=el("div","recording-row");row.append(el("span","",(d.time || dateLabel(d.timestamp,true))+" · "+Math.round(d.confidence*100)+"% confidence"));
        if(d.detail_url)row.append(link("Open recording ↗",d.detail_url));recordings.append(row);
      }
      if(data.limited)recordings.append(el("p","field-help","Showing the most recent detections; the complete list is in BirdNET."));
      $("bird-content").append(recordings);
      const sources=el("section","detail-section");sources.append(el("h3","","Artwork credits"),credits(data.artworks));$("bird-content").append(sources);
    }catch(e){if(request===birdRequest)$("bird-status").textContent=e.message;}
  }
  function svgEl(tag,attrs) {const n=document.createElementNS("http://www.w3.org/2000/svg",tag);for(const [k,v]of Object.entries(attrs || {}))n.setAttribute(k,v);return n;}
  function batteryChart(readings) {
    const host=$("battery-chart");host.replaceChildren();
    const rows=readings.filter(r=>Number.isFinite(r.battery_percent)&&Number.isFinite(Date.parse(r.at)));
    if(!rows.length)return;
    const width=360,height=150,pad=30,min=Date.parse(rows[0].at),max=Math.max(min+3600000,Date.parse(rows.at(-1).at));
    const x=r=>pad+(Date.parse(r.at)-min)/(max-min)*(width-pad-8), y=r=>12+(100-r.battery_percent)/100*(height-42);
    const svg=svgEl("svg",{viewBox:"0 0 "+width+" "+height,role:"img","aria-label":"Battery percentage over time, "+rows.length+" readings"});
    for(const p of [0,50,100]){const yy=12+(100-p)/100*(height-42);svg.append(svgEl("line",{x1:pad,y1:yy,x2:width-8,y2:yy,stroke:"#dedfd5"}));const t=svgEl("text",{x:0,y:yy+3,class:"axis"});t.textContent=p+"%";svg.append(t);}
    const path=rows.map((r,i)=>(i&&Date.parse(r.at)-Date.parse(rows[i-1].at)<4*3600000?"L":"M")+x(r).toFixed(2)+","+y(r).toFixed(2)).join(" ");
    svg.append(svgEl("path",{d:path,fill:"none",stroke:"#526c53","stroke-width":1.7}));
    for(const r of rows){const c=svgEl("circle",{cx:x(r),cy:y(r),r:2.8,fill:r.charging==="charging"?"#bb8b3f":r.charging==="unknown"?"#969991":"#526c53",class:"battery-point"});const t=svgEl("title");t.textContent=dateLabel(r.at,true)+": "+r.battery_percent+"%, "+(r.battery_voltage ?? "—")+" V, "+r.charging.replaceAll("_"," ");c.append(t);svg.append(c);}
    for(const [r,anchor]of [[rows[0],"start"],[rows.at(-1),"end"]]){const t=svgEl("text",{x:anchor==="start"?pad:width-8,y:height-4,class:"axis","text-anchor":anchor});t.textContent=dateLabel(r.at,true);svg.append(t);}
    host.append(svg);const legend=el("div","battery-legend");legend.append(el("span","","Not charging"),el("span","charging","Charging"),el("span","unknown","Unknown"));host.append(legend);
  }
  function durationRange(range) {
    if(!range||!Number.isFinite(range.low)||!Number.isFinite(range.high)||range.low<0||range.high<range.low)return null;
    const scale=range.high>=72?24:1,unit=scale===24?"days":"hours";
    return Math.max(0,Math.floor(range.low/scale))+"–"+Math.ceil(range.high/scale)+" "+unit;
  }
  function renderBatteryEstimate(estimate = {}) {
    const range=estimate.hours_to_15;
    const duration=estimate.available===true&&range?.high>0?durationRange(range):null;
    const summary=$("battery-estimate"),note=$("battery-estimate-note");
    summary.hidden=note.hidden=!duration;
    summary.textContent=duration?"Estimated time to 15%: "+duration+".":"";
    note.textContent=duration?"Voltage-based estimate.":"";
    note.title=duration?"Battery voltage changes with temperature and load. This estimate does not predict shutdown time.":"";
  }
  async function loadBattery() {
    batteryLoaded=Date.now();renderBatteryEstimate();$("battery-history-status").textContent="Loading battery history…";
    try {
      const data=await get("/api/battery?days="+$("battery-days").value);batteryChart(data.readings || []);
      $("battery-history-status").textContent=data.readings?.length?nf.format(data.readings.length)+" readings · latest "+dateLabel(data.readings.at(-1).at,true):"No battery readings yet.";
      renderBatteryEstimate(data.estimate || {});
    }catch(e){$("battery-history-status").textContent=e.message;}
  }
  $("battery-details").addEventListener("toggle",()=>{if($("battery-details").open)loadBattery();});
  $("battery-days").addEventListener("change",loadBattery);
  async function loadArchive() {
    archiveLoaded=state?.last_success || state?.revision;$("archive-status").textContent="Loading history…";
    try {
      const data=await get("/api/archive");$("archive-grid").replaceChildren();
      for(const day of data.days || []){const b=el("button","archive-card");b.type="button";const image=el("img");image.src=day.art_url;image.alt="Bird print from "+dateLabel(day.date);image.loading="lazy";b.append(image,el("strong","",dateLabel(day.date)),el("span","",day.species_count+" species · "+nf.format(day.total_detections)+" detections"));b.addEventListener("click",()=>openArchive(day));$("archive-grid").append(b);}
      $("archive-status").textContent=state?.archive_error || (data.days?.length?nf.format(data.days.length)+" "+(data.days.length===1?"day":"days"):"History starts with the first completed print.");
    }catch(e){$("archive-status").textContent=e.message;}
  }
  $("archive-details").addEventListener("toggle",()=>{if($("archive-details").open)loadArchive();});
  $("history-link").addEventListener("click",()=>{$("archive-details").open=true;});
  async function openArchive(day) {
    openDialog("archive-dialog");$("archive-dialog-heading").textContent=dateLabel(day.date);$("archive-content").replaceChildren();$("archive-dialog-status").textContent="Loading saved print…";
    try {
      const s=await get(day.state_url),host=$("archive-content");const image=el("img","archive-detail-image");image.src=day.art_url;image.alt="Archived bird print from "+dateLabel(day.date);host.append(image);
      const links=el("div","detail-links"),download=link("Download artwork ↓",day.art_url);
      download.download="heard-today-"+day.date+".png";download.removeAttribute("target");
      links.append(download,link("E-paper preview ↗",day.preview_url),link("Download print & data ↓",day.download_url));host.append(links);
      const birds=el("div","archive-birds");for(const b of s.stats?.species || []){const button=el("button","",b.common_name+" · "+nf.format(b.count));button.type="button";button.addEventListener("click",()=>openBird(b.scientific_name,day.date,"archive"));birds.append(button);}host.append(birds);
      const sources=el("section","detail-section");sources.append(el("h3","","Credits saved with this print"));
      if(s.print_rights?.rights){const notice=el("p","small");notice.append(document.createTextNode("This composed print: "),link(s.print_rights.rights,s.print_rights.rights_url),document.createTextNode(". "+(s.print_rights.changes || "")));sources.append(notice);}
      sources.append(credits(s.artwork_credits || s.photo_credits || s.artworks || []));host.append(sources);
      $("archive-dialog-status").textContent="Saved "+dateLabel(day.observed_at,true)+" · "+day.species_count+" species.";
    }catch(e){$("archive-dialog-status").textContent=e.message;}
  }
  $("packs-details").addEventListener("toggle",async()=>{
    if(!$("packs-details").open)return;$("packs-status").textContent="Loading artwork collections…";
    try {
      const data=await get("/api/artwork-packs");$("packs-list").replaceChildren();
      for(const p of data.packs || []) {
        const card=el("article","pack-card");card.append(el("h3","",p.title || p.name || p.id),el("p","small muted",p.description || "Individually reviewed historical drawings."));
        card.append(el("p","small",(p.installed_count ?? 0)+" installed · "+(p.reviewed_count ?? p.asset_count ?? 0)+" reviewed drawings"));
        const links=el("div","pack-links");if(p.source_url || p.url)links.append(link("View collection ↗",p.source_url || p.url));if(p.license_url || p.rights_url)links.append(link(p.license || "License",p.license_url || p.rights_url));card.append(links);
        card.append(el("p","field-help",p.attribution || "Credits and source links accompany each drawing. Downloads retain the collection’s license terms."));
        if(p.install_command){const details=el("details");details.append(el("summary","small","Download on your dashboard server"),el("pre","",p.install_command),el("p","field-help","The download is separate from FederFunk’s bundled library. Restart the companion after installing a collection."));card.append(details);}
        $("packs-list").append(card);
      }
      $("packs-status").textContent="Only reviewed, installed drawings are used. Your selected illustrations take precedence.";
    }catch(e){$("packs-status").textContent=e.message;}
  });
})();
