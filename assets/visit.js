(function(){
  var API="https://naami-xann.onrender.com/api/zoe_visits/ping";
  var vid;try{vid=localStorage.getItem("zp_vid");if(!vid){vid=Math.random().toString(36).slice(2)+Date.now().toString(36);localStorage.setItem("zp_vid",vid)}}catch(e){vid=Math.random().toString(36).slice(2)+Date.now().toString(36)}
  function ping(keep){try{(new Image()).src=API+"?v="+encodeURIComponent(vid)+(keep?"&k=1":"")+"&t="+Date.now()}catch(e){}}
  ping(false);
  setInterval(function(){if(document.visibilityState!=="hidden")ping(true)},30000);
})();
