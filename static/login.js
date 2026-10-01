const f=document.getElementById('form');
const b=document.getElementById('btn');
const e=document.getElementById('err');
const c=document.getElementById('cfg');
const forgot=document.getElementById('forgot');
const recovery=document.getElementById('recovery');
const recoveryForm=document.getElementById('recoveryForm');
const recoveryUser=document.getElementById('recoveryUser');
const recoveryCode=document.getElementById('recoveryCode');
const recoveryBtn=document.getElementById('recoveryBtn');
const recoveryErr=document.getElementById('recoveryErr');
const recoveryOk=document.getElementById('recoveryOk');
const backLogin=document.getElementById('backLogin');
let recoveryAvailable=false;

function show(el,msg=''){
  if(msg) el.textContent=msg;
  el.style.display='block';
}
function hide(el){el.style.display='none'}
function openRecovery(){
  f.hidden=true;
  recovery.hidden=false;
  hide(recoveryErr);hide(recoveryOk);
  recoveryUser.value=document.getElementById('user').value||'';
  if(!recoveryAvailable){
    show(recoveryErr,'A recuperação ainda não está configurada. Para entrar agora, altere CTI_ACCESS_PASSWORD na Vercel e faça um novo deploy.');
    recoveryBtn.disabled=true;
  }else{
    recoveryBtn.disabled=false;
    setTimeout(()=>recoveryCode.focus(),0);
  }
}
function closeRecovery(){
  recovery.hidden=true;
  f.hidden=false;
  recoveryCode.value='';
  hide(recoveryErr);hide(recoveryOk);
}

async function status(){
  try{
    const r=await fetch('/api/auth/status',{cache:'no-store'}),j=await r.json();
    if(j.authenticated)location.replace('/');
    recoveryAvailable=!!j.recovery_available;
    if(j.configured===false){
      show(c,'Acesso temporariamente indisponível. Tente novamente mais tarde.');
      b.disabled=true;
    }
  }catch{}
}

f.addEventListener('submit',async ev=>{
  ev.preventDefault();hide(e);b.disabled=true;b.textContent='Entrando…';
  try{
    const r=await fetch('/api/auth/login',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({username:document.getElementById('user').value,password:document.getElementById('pass').value})
    });
    const j=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(j.detail||'Não foi possível entrar.');
    location.replace('/');
  }catch(x){
    show(e,x.message);b.disabled=false;b.textContent='Entrar';
  }
});

forgot.addEventListener('click',openRecovery);
backLogin.addEventListener('click',closeRecovery);
recoveryForm.addEventListener('submit',async ev=>{
  ev.preventDefault();hide(recoveryErr);hide(recoveryOk);recoveryBtn.disabled=true;recoveryBtn.textContent='Verificando…';
  try{
    const r=await fetch('/api/auth/recover',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({username:recoveryUser.value,recovery_code:recoveryCode.value})
    });
    const j=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(j.detail||'Não foi possível recuperar o acesso.');
    recoveryCode.value='';
    const mins=Math.max(5,Math.round((j.expires_in||1800)/60));
    show(recoveryOk,`Acesso recuperado por ${mins} minutos. Entre no CTI e depois altere CTI_ACCESS_PASSWORD na Vercel.`);
    recoveryBtn.textContent='Entrando…';
    setTimeout(()=>location.replace('/?recovery=1'),1200);
  }catch(x){
    show(recoveryErr,x.message);recoveryBtn.disabled=false;recoveryBtn.textContent='Recuperar acesso';
  }
});
status();
