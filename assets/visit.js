(function(){
  var BASE="https://naami-xann.onrender.com/api/zoe_visits/";
  var vid;try{vid=localStorage.getItem("zp_vid");if(!vid){vid=Math.random().toString(36).slice(2)+Date.now().toString(36);localStorage.setItem("zp_vid",vid)}}catch(e){vid=Math.random().toString(36).slice(2)+Date.now().toString(36)}
  // traffic source of this visit: utm_source / src / ref param, else referrer host (raw; classified on the server).
  // First page of the session decides; internal navigation keeps it. Instagram / Pinterest / blog etc need NO change here.
  var src="";
  try{
    src=sessionStorage.getItem("zp_src");
    if(src===null){
      var q=new URLSearchParams(location.search),h="";
      src=(q.get("utm_source")||q.get("src")||q.get("ref")||"").slice(0,40);
      if(!src&&document.referrer){try{h=new URL(document.referrer).hostname}catch(e){}if(h&&h!==location.hostname)src=h.slice(0,40)}
      sessionStorage.setItem("zp_src",src);
    }
  }catch(e){src=src||""}
  function ping(keep){try{(new Image()).src=BASE+"ping?v="+encodeURIComponent(vid)+(keep?"&k=1":"")+"&r="+encodeURIComponent(src||"")+"&t="+Date.now()}catch(e){}}
  ping(false);
  setInterval(function(){if(document.visibilityState!=="hidden")ping(true)},30000);
  // Amazon link click counter (site stays direct-to-Amazon, affiliate tag untouched; this is just an anonymous beacon)
  var AMZ=/(^|\.)amazon\.[a-z.]{2,6}$|(^|\.)amzn\.(to|com)$|^a\.co$/i;
  function onClick(e){
    try{
      if(e.type==="auxclick"&&e.button!==1)return;
      var a=e.target&&e.target.closest?e.target.closest("a[href]"):null;if(!a)return;
      var u=new URL(a.href,location.href);if(!AMZ.test(u.hostname))return;
      var m=u.pathname.match(/\/(?:dp|gp\/product)\/([A-Z0-9]{10})/i);
      var pg=location.pathname.replace(/index\.html$/,"").replace(/^\/|\/$/g,"")||"home";
      var url=BASE+"click?v="+encodeURIComponent(vid)+"&a="+encodeURIComponent(m?m[1].toUpperCase():"")+"&pg="+encodeURIComponent(pg)+"&r="+encodeURIComponent(src||"")+"&t="+Date.now();
      if(!(navigator.sendBeacon&&navigator.sendBeacon(url)))(new Image()).src=url;
    }catch(err){}
  }
  document.addEventListener("click",onClick,true);
  document.addEventListener("auxclick",onClick,true);
})();
