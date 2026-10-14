import { request, serverStorage } from './server-storage.js';
export const accents = { copper: ['Медный','#dfa080'], violet: ['Фиолетовый','#b29af8'], blue: ['Голубой','#88b8ff'], mint: ['Мятный','#83d8c0'], rose: ['Розовый','#eda2c5'], amber: ['Янтарный','#e8bd82'], silver: ['Серебряный','#b8c3d6'] };
export const defaults = { name:'Личное пространство',bio:'',collection:'Мой архив',accent:'copper',theme:'dark',motion:true,avatar:'' };
export let profile={...defaults};
let version=0;
const systemTheme=matchMedia('(prefers-color-scheme: light)');
const lightAccents={copper:'#a5562c',violet:'#7551b5',blue:'#32649e',mint:'#24765d',rose:'#a44774',amber:'#8b621e',silver:'#58687d'};
function applyTheme(){
 const theme=profile.theme==='system'?(systemTheme.matches?'light':'dark'):profile.theme;
 document.documentElement.dataset.theme=theme;
 document.documentElement.style.setProperty('--accent',theme==='light'?(lightAccents[profile.accent]||lightAccents.copper):(accents[profile.accent]?.[1]||accents.copper[1]));
}
systemTheme.addEventListener('change',applyTheme);
export function applyProfile(value) {
  profile={...defaults,...value};
  if(!['dark','light','system'].includes(profile.theme))profile.theme='dark';
  applyTheme();
  document.documentElement.dataset.motion=profile.motion?'on':'off';
}
export async function loadProfile() {
  if(serverStorage) {const data=await (await request('profile')).json();version=data.version;applyProfile(data.profile);}
  else {try{applyProfile(JSON.parse(localStorage.getItem('hxry-profile-v1'))||defaults);}catch{applyProfile(defaults);}}
}
export async function saveProfile(value) {
  if(serverStorage){const data=await (await request('profile',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({version,profile:value})})).json();version=data.version;applyProfile(data.profile);}
  else {localStorage.setItem('hxry-profile-v1',JSON.stringify(value));applyProfile(value);}
}
export async function avatarFromFile(file) {
  if(!['image/png','image/jpeg','image/webp','image/gif'].includes(file.type)||file.size>5*1024**2)throw new Error('Выберите PNG, JPEG, WebP или GIF размером до 5 МБ.');
  const image=await createImageBitmap(file);
  try {
    const canvas=document.createElement('canvas');canvas.width=160;canvas.height=160;
    const context=canvas.getContext('2d');const side=Math.min(image.width,image.height);
    context.drawImage(image,(image.width-side)/2,(image.height-side)/2,side,side,0,0,160,160);
    const result=canvas.toDataURL('image/png');
    if(result.length>136000)throw new Error('Изображение слишком сложное. Выберите другое.');
    return result;
  } finally {image.close();}
}
