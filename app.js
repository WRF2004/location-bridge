const $ = id => document.getElementById(id);
const incoming = new URLSearchParams(location.hash.slice(1)).get('token');
if (incoming) localStorage.setItem('bridge-token', incoming);
let token = incoming || localStorage.getItem('bridge-token') || '';
let initialized = false, lastState, pending = false, qrLoaded = false;
let editingId = null, placesSignature = '', shutdownPending = false;
let mapCenter = {lat:37.7749, lon:-122.4194}, zoom = 12, mapUsed = false;
const notice = text => { $('toast').textContent=text; $('toast').hidden=false; clearTimeout(notice.timer); notice.timer=setTimeout(()=>$('toast').hidden=true,4500); };
async function api(path, body) {
  const controller = new AbortController(), timeout=setTimeout(()=>controller.abort(), 12000);
  try {
    const response = await fetch(path, {method:body?'POST':'GET', headers:{Authorization:'Bearer '+token,...(body?{'Content-Type':'application/json'}:{})}, body:body?JSON.stringify(body):undefined, signal:controller.signal});
    if(response.status===401){if(!$('login').open)$('login').showModal(); throw Error('请重新扫描 Mac 的连接二维码');}
    if(!response.ok){const data=await response.json(); throw Error(data.error||'连接失败');}
    return path.endsWith('.svg')?await response.text():await response.json();
  } finally {clearTimeout(timeout);}
}
async function action(name, data={}) {
  if(pending)return false;
  pending=true; document.querySelectorAll('[data-action]').forEach(b=>b.disabled=true);
  if(name==='shutdown')shutdownPending=true;
  try {
    await api('/api/action',{action:name,...data});
    for(let i=0;i<720;i++){
      const state=await refresh();
      if(!state)return false;
      if(!state.busy){if(state.error){shutdownPending=false;notice(state.error);return false;}return true;}
      await new Promise(resolve=>setTimeout(resolve,500));
    }
    notice('操作仍在进行，请查看设备状态。');return false;
  }
  catch(err){shutdownPending=false;notice(err.message);return false;}
  finally {pending=false; await refresh();}
}
document.querySelectorAll('[data-action]').forEach(button=>button.addEventListener('click',()=>action(button.dataset.action)));
function renderPlaces(s){
  const signature=JSON.stringify([s.places,s.active_place,s.selected_id,s.mode,s.busy,pending]);
  if(signature===placesSignature)return;
  placesSignature=signature;
  const fragment=document.createDocumentFragment();
  for(const place of s.places){
    const active=s.mode==='active'&&s.active_place?.id===place.id;
    const row=document.createElement('div');row.className='saved-place'+(active?' current':'');
    const choose=document.createElement('button');choose.className='place-choose';choose.dataset.place=place.id;
    choose.disabled=s.busy||pending||!['idle','active'].includes(s.mode);
    const name=document.createElement('strong');name.textContent=place.name;
    const coords=document.createElement('small');coords.textContent=`${place.latitude.toFixed(5)}, ${place.longitude.toFixed(5)}`;
    const label=document.createElement('span');label.className='place-label';
    const unchanged=active&&JSON.stringify(s.active_place)===JSON.stringify(place);
    label.textContent=unchanged?'● 正在使用':active?'应用已保存的修改 ↗':'点击使用 ↗';
    choose.append(name,coords,label);
    const controls=document.createElement('div');controls.className='place-controls';
    for(const [verb,text] of [['edit','编辑'],['delete','删除']]){
      const button=document.createElement('button');button.className='text-button';button.textContent=text;
      button.dataset[verb]=place.id;button.disabled=s.busy||pending||(verb==='delete'&&s.active_place?.id===place.id);
      button.setAttribute('aria-label',text+' '+place.name);controls.append(button);
    }
    row.append(choose,controls);fragment.append(row);
  }
  $('places-list').replaceChildren(fragment);
  $('places-empty').hidden=s.places.length>0;
}
function render(s){
  lastState=s;
  const modes={idle:'模拟定位已关闭',starting:'正在设置',active:'模拟定位已开启',unknown:'需要恢复'};
  $('badge').textContent=s.busy?'正在处理…':modes[s.mode]||s.mode;
  $('badge').className='badge '+s.mode;
  $('device').textContent=s.device; $('message').textContent=s.message;
  const displayed=s.active_place||s.place;
  $('place-name').textContent=displayed.name;
  const configured=Number.isFinite(s.place.latitude)&&Number.isFinite(s.place.longitude);
  $('coordinates').textContent=Number.isFinite(displayed.latitude)?`${displayed.latitude.toFixed(6)}, ${displayed.longitude.toFixed(6)} · WGS84`:'添加地点后即可使用';
  document.querySelectorAll('[data-action]').forEach(b=>b.disabled=s.busy||pending);
  $('toggle').disabled=s.busy||pending||(!configured&&s.mode==='idle')||s.mode==='starting';
  $('toggle').setAttribute('aria-checked',String(s.mode==='active'));
  $('toggle').classList.toggle('is-on',s.mode==='active');
  $('toggle-label').textContent=s.mode==='active'?'关闭模拟定位':s.mode==='unknown'?'恢复真实定位':'开启模拟定位';
  $('toggle-hint').textContent=s.mode==='active'?'点击恢复真实位置':s.mode==='unknown'?'上次状态未确认，请先恢复':'使用上方地点，或点击下方卡片';
  document.querySelector('#place-form button[type=submit]').disabled=s.busy||pending;
  $('timer').hidden=s.elapsed===null;
  if(s.elapsed!==null)$('timer').textContent=`已开启 ${Math.floor(s.elapsed/60)} 分钟 · 手动关闭，无倒计时`;
  renderPlaces(s);
  $('logs').textContent=s.logs.join('\n')||'暂无记录';
  if(!initialized){
    initialized=true;
    if(configured){mapCenter={lat:s.place.latitude,lon:s.place.longitude};}
    else {$('setup').open=true;$('place-editor').open=true;}
    if(innerWidth>700){$('pairing').parentNode.prepend($('pairing'));$('pairing').open=true;}
    drawMap();
  }
}
async function refresh(){
  if(!token){if(!$('login').open)$('login').showModal();return;}
  try {const state=await api('/api/status');render(state);if(!qrLoaded)loadQR();return state;}
  catch(err){$('badge').textContent=shutdownPending?'程序已断开':'连接中断';$('badge').className='badge unknown';$('message').textContent=shutdownPending?'退出流程已结束或连接已中断。请在手机地图核对位置；双击 Start.command 可重新启动。':'无法连接 Mac。请确认电脑和工具仍在运行、设备处于同一局域网。';document.querySelectorAll('[data-action],.place-choose').forEach(b=>b.disabled=true);$('toggle').disabled=true;return null;}
}
async function loadQR(){
  qrLoaded=true;
  try {const svg=await api('/api/pairing.svg');$('qr').src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(svg);}
  catch(err){qrLoaded=false;}
}
$('login-form').addEventListener('submit',async event=>{event.preventDefault();let value=$('access').value.trim();try{if(value.includes('://'))value=new URLSearchParams(new URL(value).hash.slice(1)).get('token')||'';}catch{}token=value;localStorage.setItem('bridge-token',token);$('login').close();await refresh();});
function editPlace(place=null){
  editingId=place?.id||null;
  $('editor-title').textContent=place?'编辑地点':'添加地点';
  $('name').value=place?.name||'';$('lat').value=place?.latitude??'';$('lon').value=place?.longitude??'';
  $('confirm-place').checked=false;
  if(place)mapCenter={lat:place.latitude,lon:place.longitude};
  $('place-editor').open=true;$('place-editor').scrollIntoView({behavior:'smooth',block:'start'});drawMap();
}
$('add-place').onclick=()=>editPlace();
$('cancel-edit').onclick=()=>{editPlace();$('place-editor').open=false;};
$('toggle').onclick=()=>action(lastState?.mode==='idle'?'start':'clear');
$('places-list').onclick=event=>{
  const button=event.target.closest('button');if(!button||button.disabled)return;
  const {place,edit,delete:id}=button.dataset;
  if(place)action('start',{id:place});
  if(edit)editPlace(lastState.places.find(p=>p.id===edit));
  if(id){const saved=lastState.places.find(p=>p.id===id);if(confirm(`删除“${saved.name}”？`))action('delete',{id});}
};
$('place-editor').addEventListener('toggle',()=>{if($('place-editor').open)drawMap();});
async function copy(text){
  if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(text);notice('已复制');return;}
  const input=document.createElement('textarea');input.value=text;input.style.position='fixed';input.style.top='0';document.body.append(input);input.focus();input.select();input.setSelectionRange(0,text.length);
  const ok=document.execCommand('copy');input.remove();if(ok)notice('已复制');else prompt('请长按复制以下链接',text);
}
$('copy-link').onclick=async()=>{try{await copy((await api('/api/link')).url);}catch(e){notice(e.message);}};
$('place-form').onsubmit=async event=>{
  event.preventDefault();if(!$('confirm-place').checked)return;
  const ok=await action('save',{...(editingId?{id:editingId}:{}),name:$('name').value,latitude:Number($('lat').value),longitude:Number($('lon').value)});
  if(ok){editingId=null;$('place-form').reset();$('editor-title').textContent='添加地点';$('place-editor').open=false;notice('地点已保存，点击卡片即可使用');}
};
// Small raster map picker; no third-party JS, key, search service, or location tracking.
function project(lat,lon){let sin=Math.sin(Math.max(-85,Math.min(85,lat))*Math.PI/180),n=256*2**zoom;return{x:(lon+180)/360*n,y:(.5-Math.log((1+sin)/(1-sin))/(4*Math.PI))*n};}
function unproject(x,y){const n=256*2**zoom;return{lat:Math.atan(Math.sinh(Math.PI*(1-2*y/n)))*180/Math.PI,lon:((x/n*360)%360+540)%360-180};}
let drawId=0;
function drawMap(){
  const box=$('map'),w=box.clientWidth,h=box.clientHeight;if(!w||!h)return;
  const c=project(mapCenter.lat,mapCenter.lon),origin={x:c.x-w/2,y:c.y-h/2};
  const current=++drawId, fragment=document.createDocumentFragment();let loaded=0,failed=0;
  for(let x=Math.floor(origin.x/256);x<=Math.floor((origin.x+w)/256);x++)for(let y=Math.floor(origin.y/256);y<=Math.floor((origin.y+h)/256);y++){
    if(y<0||y>=2**zoom)continue;
    const img=new Image();img.referrerPolicy='strict-origin-when-cross-origin';img.draggable=false;img.alt='';img.style.left=(x*256-origin.x)+'px';img.style.top=(y*256-origin.y)+'px';
    img.onload=()=>{if(current===drawId){loaded++;$('map-note').hidden=true;}};
    img.onerror=()=>{if(current===drawId&&!loaded){failed++;$('map-note').hidden=false;$('map-note').textContent='地图暂时无法加载，请填写已核实的 WGS84 经纬度。';}};
    img.src=`https://tile.openstreetmap.org/${zoom}/${((x%2**zoom)+2**zoom)%2**zoom}/${y}.png`;fragment.append(img);
  }
  $('tiles').replaceChildren(fragment);
}
function updateCoordinates(){mapUsed=true;$('lat').value=mapCenter.lat.toFixed(6);$('lon').value=mapCenter.lon.toFixed(6);$('confirm-place').checked=false;}
let drag=null, frame=null;
$('map').addEventListener('pointerdown',event=>{if(event.target.closest('button,a'))return;drag={x:event.clientX,y:event.clientY,c:project(mapCenter.lat,mapCenter.lon),moved:false};$('map').setPointerCapture(event.pointerId);});
$('map').addEventListener('pointermove',event=>{if(!drag)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;if(Math.abs(dx)+Math.abs(dy)>3)drag.moved=true;mapCenter=unproject(drag.c.x-dx,drag.c.y-dy);updateCoordinates();if(!frame)frame=requestAnimationFrame(()=>{drawMap();frame=null;});});
$('map').addEventListener('pointerup',event=>{if(!drag)return;if(!drag.moved){const box=$('map').getBoundingClientRect();mapCenter=unproject(drag.c.x+event.clientX-box.left-box.width/2,drag.c.y+event.clientY-box.top-box.height/2);updateCoordinates();drawMap();}drag=null;});
$('map').addEventListener('pointercancel',()=>drag=null);
$('zoom-in').onclick=()=>{zoom=Math.min(19,zoom+1);drawMap();};$('zoom-out').onclick=()=>{zoom=Math.max(3,zoom-1);drawMap();};
for(const id of ['lat','lon'])$(id).addEventListener('change',()=>{const lat=Number($('lat').value),lon=Number($('lon').value);$('confirm-place').checked=false;if(Number.isFinite(lat)&&Number.isFinite(lon)&&Math.abs(lat)<=85&&Math.abs(lon)<=180){mapCenter={lat,lon};drawMap();}});
addEventListener('resize',drawMap);
refresh();setInterval(refresh,2200);
