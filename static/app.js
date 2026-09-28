/* ============================================================ utilidades */
const $ = s => document.querySelector(s), $$ = s => [...document.querySelectorAll(s)];
const LETRAS = 'ABCDEFGH'.split('');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const safeHref = u => { try { const x = new URL(String(u || ''), location.origin); return (x.protocol === 'https:' || x.protocol === 'http:') ? x.href : '#'; } catch { return '#'; } };
const hoje = () => new Date().toLocaleDateString('sv-SE');
const addDias = (iso, n) => { const d = new Date(iso + 'T12:00:00'); d.setDate(d.getDate() + n); return d.toLocaleDateString('sv-SE'); };
const fmtData = iso => new Date(iso + 'T12:00:00').toLocaleDateString('pt-BR', {weekday:'short', day:'2-digit', month:'2-digit'});
const pct = (a, n) => n ? Math.round(100 * a / n) : 0;
const ls = { get:(k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } }, set:(k, v) => localStorage.setItem(k, JSON.stringify(v)) };
let IAC = ls.get('cti_ia_cache', {});
const iaCache = id => IAC[id];
function iaGuardar(id, x){
  if (!x || x.fonte !== 'ia') return;
  IAC[id] = x; const ks = Object.keys(IAC); if (ks.length > 1500) delete IAC[ks[0]];
  try { ls.set('cti_ia_cache', IAC); } catch { const k = Object.keys(IAC); k.slice(0, 300).forEach(i => delete IAC[i]); try { ls.set('cti_ia_cache', IAC); } catch {} }
}
function toast(m){ const t = $('#toast'); t.textContent = m; t.classList.add('on'); clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('on'), 2400); }
const cookieVal = n => document.cookie.split('; ').find(x => x.startsWith(n + '='))?.split('=').slice(1).join('=') || '';
async function api(url, opt={}){
  const cfg = Object.assign({credentials:'same-origin', cache:'no-store'}, opt || {});
  let r; try { r = await fetch(url, cfg); } catch { throw new Error('sem conexão com a internet. Esta ação precisa estar on-line.'); }
  if (r.status === 401){ location.replace('/?reason=session'); throw new Error('Sessão expirada. Entre novamente.'); }
  if (!r.ok){ let m = await r.text(); try { m = JSON.parse(m).detail || m; } catch {} throw new Error(typeof m === 'string' ? m : 'falha na requisição'); }
  return r.json(); }
const post = (url, body, extraHeaders={}) => api(url, {method:'POST', headers:Object.assign({'Content-Type':'application/json','X-CSRF-Token':decodeURIComponent(cookieVal('cti_csrf'))}, extraHeaders), body:JSON.stringify(body)});
const carregarLote = ids => post('/api/questoes/lote', {ids}).then(r => r.items);
const corAcerto = p => p >= 70 ? 'var(--ok)' : p >= 50 ? 'var(--warn)' : 'var(--bad)';
const ic = n => `<svg class="i"><use href="#i-${n}"/></svg>`;
const AREA_ICON = {'Clínica Médica':'steth','Cirurgia Geral':'scalpel','Pediatria':'baby','Ginecologia e Obstetrícia':'venus','Medicina Preventiva e Coletiva':'shield'};
const AREA_CURTA = {'Clínica Médica':'Clínica','Cirurgia Geral':'Cirurgia','Pediatria':'Pediatria','Ginecologia e Obstetrícia':'GO','Medicina Preventiva e Coletiva':'Preventiva'};

/* ============================================================ estado persistente */
// medquest_historico: [{id,data,ts,area,tema,exame,acertou,origem}]
// medquest_erros: [{id,area,tema,adicionado,estagio(0-3),proxima,dominada,erros}]
// medquest_missao: {streak,ultimoConcluido,vistas:[ids],dias:{data:{ids,respostas:{id:{sel,acertou}},concluida,modo}}}
let HIST = ls.get('medquest_historico', []);
let ERROS = ls.get('medquest_erros', []);
let MISSAO = ls.get('medquest_missao', {streak:0, ultimoConcluido:null, vistas:[], dias:{}});
// migração do formato antigo (objetos de questão completos)
ERROS = ERROS.map(e => e.enunciado !== undefined ? {id:e.id, area:e.especialidade, tema:e.tema || '', adicionado:hoje(), estagio:0, proxima:hoje(), dominada:false, erros:1} : e);
const salvar = () => { ls.set('medquest_historico', HIST.slice(-20000)); ls.set('medquest_erros', ERROS); ls.set('medquest_missao', MISSAO); atualizarHeader(); };

let STATS = null, INDICE = [], IDX = {}, REFORCO = null;

/* ============================================================ navegação interna do PWA */
const ROTAS = new Set(['painel','missao','banco','simulado','erros','favoritos','desempenho','reforco','segunda','atualizacoes','quiz']);
let NAV_READY = false;
function viewAtual(){
  const v = $('.view.on');
  return v ? v.id.replace('view-', '') : 'painel';
}
function estadoRota(v){ return {cti:true, view:v}; }
function registrarRota(v, substituir=false){
  if (!NAV_READY || !ROTAS.has(v)) return;
  const hash = '#' + v;
  if (substituir || (history.state?.cti && history.state.view === v)) history.replaceState(estadoRota(v), '', hash);
  else history.pushState(estadoRota(v), '', hash);
}
function confirmarSaidaProva(){
  return !(S && S.modo === 'prova' && !S.finalizado && Object.keys(S.selecao || {}).length) ||
    confirm('Sair do simulado? As respostas não serão corrigidas.');
}
function aoVoltarDoSistema(e){
  const alvo = e.state?.cti && ROTAS.has(e.state.view) ? e.state.view : 'painel';
  const atual = viewAtual();
  if (atual === 'quiz' && alvo !== 'quiz' && !confirmarSaidaProva()){
    history.pushState(estadoRota('quiz'), '', '#quiz');
    return;
  }
  if (atual === 'segunda' && alvo !== 'segunda' && typeof F2S !== 'undefined' && F2S.est && F2S.fase === 'estacao'){
    if (!confirm('Sair da estação sem avaliar?')){
      history.pushState(estadoRota('segunda'), '', '#segunda');
      return;
    }
    clearInterval(F2S.h); F2S.est = null; F2S.fase = '';
  }
  if (alvo === 'quiz'){
    if (!S){
      history.replaceState(estadoRota('painel'), '', '#painel');
      mostrar('painel', {semHistorico:true});
      return;
    }
    mostrar('quiz', {semHistorico:true});
    renderQuestao();
    return;
  }
  mostrar(alvo, {semHistorico:true});
}

/* ============================================================ inicialização */
async function init(){
  history.replaceState(estadoRota('painel'), '', '#painel');
  NAV_READY = true;
  window.addEventListener('popstate', aoVoltarDoSistema);
  if (ls.get('medquest_theme') === 'dark') document.documentElement.dataset.theme = 'dark';
  const alternaTema = () => { const d = document.documentElement.dataset.theme === 'dark'; document.documentElement.dataset.theme = d ? '' : 'dark'; ls.set('medquest_theme', d ? 'light' : 'dark'); $('#sheet').classList.remove('on'); if ($('#view-painel').classList.contains('on')) renderPainel(); };
  $('#btnTema').onclick = alternaTema; $('#shTema').onclick = alternaTema;
  const sairSessao = async () => {
    if (!confirm('Sair da sessão do CTi? Seu progresso local será preservado.')) return;
    try { await post('/api/auth/logout', {}); } catch {}
    try { navigator.serviceWorker?.controller?.postMessage({type:'CLEAR_PRIVATE'}); } catch {}
    location.replace('/');
  };
  $('#btnLogout').onclick = sairSessao; $('#shLogout').onclick = () => { $('#sheet').classList.remove('on'); sairSessao(); };
  $$('#tabs button, #side button[data-v], #bnav button[data-v]').forEach(b => b.onclick = () => b.dataset.v === 'mais' ? $('#sheet').classList.add('on') : mostrar(b.dataset.v));
  $$('[data-go]').forEach(b => b.onclick = () => mostrar(b.dataset.go));
  $('#sheet').onclick = e => { if (e.target.id === 'sheet') $('#sheet').classList.remove('on'); };
  $('#brandHome').onclick = () => mostrar('painel');
  $('#btnBusca').onclick = () => { mostrar('banco'); setTimeout(() => $('#bBusca').focus(), 200); };
  $('#btnSino').onclick = () => mostrar('erros');
  $('#btnPerfil').onclick = editarPerfil; $('#shPerfil').onclick = () => { $('#sheet').classList.remove('on'); editarPerfil(); };
  aplicarPerfil();
  [STATS, INDICE] = await Promise.all([api('/api/stats'), api('/api/indice')]);
  INDICE.forEach(i => IDX[i.id] = i);
  preencherFiltros();
  atualizarHeader();
  renderMissao();
  renderPainel();
  document.addEventListener('keydown', teclas);
  if ('serviceWorker' in navigator) navigator.serviceWorker.getRegistrations().then(rs => rs.forEach(r => { if (r.scope.endsWith('/static/')) r.unregister(); })).catch(() => {}); navigator.serviceWorker.register('/sw.js', {scope:'/'}).catch(() => {});
}

function opcoes(sel, valores, todos = 'Todas', rotulo = v => v){ sel.innerHTML = (todos ? `<option value="">${todos}</option>` : '') + valores.map(v => `<option value="${esc(v)}">${esc(rotulo(v))}</option>`).join(''); }
function ligarAreaTema(areaSel, temaSel, onChange){
  opcoes(areaSel, STATS.especialidades);
  const upd = () => { const a = areaSel.value; const temas = a ? STATS.temas[a] : Object.values(STATS.temas).flat().sort(); opcoes(temaSel, temas, 'Todos'); onChange && onChange(); };
  areaSel.onchange = upd; upd();
  if (onChange) temaSel.onchange = onChange;
}
function preencherFiltros(){
  for (const s of ['#mExame', '#sExame', '#bExame']) opcoes($(s), STATS.exames, 'Todas as provas');
  opcoes($('#sEdicao'), STATS.edicoes); opcoes($('#bEdicao'), STATS.edicoes);
  const syncEd = (ex, ed) => { $(ex).onchange = () => { const e = $(ex).value; opcoes($(ed), STATS.edicoes.filter(x => !e || INDICE.some(i => i.edicao === x && i.exame === e))); }; };
  syncEd('#sExame', '#sEdicao'); syncEd('#bExame', '#bEdicao');
  ligarAreaTema($('#mArea'), $('#mTema'));
  ligarAreaTema($('#sArea'), $('#sTema'));
  ligarAreaTema($('#bArea'), $('#bTema'));
  ligarAreaTema($('#eArea'), $('#eTema'), renderErros);
  opcoes($('#rArea'), STATS.especialidades);
  const anos = [...new Set(STATS.edicoes.map(e => (e.match(/20\d\d/) || [])[0]).filter(Boolean))].sort().reverse();
  opcoes($('#bAno'), anos, 'Todos os anos'); opcoes($('#fxAno'), anos, 'Todos os anos');
  opcoes($('#fxExame'), STATS.exames, 'Todas as provas'); opcoes($('#fxArea'), STATS.especialidades, 'Todas');
  $('#fxBuscar').onclick = () => abrirBanco({exame:$('#fxExame').value, area:$('#fxArea').value, ano:$('#fxAno').value});
  opcoes($('#dEvArea'), STATS.especialidades, 'Todas as áreas');
}

function mostrar(v, opts = {}){
  if (!ROTAS.has(v)) v = 'painel';
  const atualAntes = viewAtual();
  if (!opts.semHistorico && atualAntes === 'segunda' && v !== 'segunda' && typeof F2S !== 'undefined' && F2S.est && F2S.fase === 'estacao'){
    if (!confirm('Sair da estação sem avaliar?')) return;
    clearInterval(F2S.h); F2S.est = null; F2S.fase = '';
  }
  $('#sheet').classList.remove('on');
  $$('.view').forEach(x => x.classList.toggle('on', x.id === 'view-' + v));
  $$('#tabs button, #side button[data-v], #bnav button[data-v]').forEach(b => b.classList.toggle('on', b.dataset.v === v));
  if (v === 'painel') renderPainel();
  if (v === 'favoritos') renderFavoritos();
  if (v === 'missao') renderMissao();
  if (v === 'erros') renderErros();
  if (v === 'banco' && !$('#bLista').children.length && STATS) buscarBanco(true);
  if (v === 'desempenho') renderDesempenho();
  if (v === 'reforco') renderReforco();
  if (v === 'segunda') renderFase2();
  if (v === 'atualizacoes') renderAtualizacoes();
  if (!opts.semHistorico) registrarRota(v, !!opts.substituir);
  window.scrollTo({top:0, behavior:opts.semHistorico ? 'auto' : 'smooth'});
}

function atualizarHeader(){
  const st = streakAtual();
  $('#hdrStreak span').textContent = st;
  const pend = ERROS.filter(e => !e.dominada && e.proxima <= hoje()).length;
  for (const b of ['#badgeErros', '#badgeSino', '#badgeSide', '#badgeMais']){ $(b).hidden = !pend; $(b).textContent = pend; }
  const m = MISSAO.dias[hoje()], resp = m ? Object.keys(m.respostas).length : 0, tot = m ? m.ids.length : 15;
  $('#sideMissao').textContent = m && m.concluida ? `${resp}/${tot} ✓` : `${resp}/${tot}`; $('#sideMissaoBar').style.width = pct(resp, tot) + '%';
  $('#contSub').textContent = m && m.concluida ? 'Concluída hoje! Que tal uma missão extra?' : m ? `Continue: ${resp}/${tot} respondidas` : '15 questões inéditas hoje';
}
function streakAtual(){ const u = MISSAO.ultimoConcluido; return (u === hoje() || u === addDias(hoje(), -1)) ? MISSAO.streak : 0; }

/* ============================================================ registro de respostas */
function registrar(q, acertou, origem){
  const i = IDX[q.id] || {};
  HIST.push({id:q.id, data:hoje(), ts:Date.now(), area:i.area || q.especialidade, tema:i.tema || q.tema, exame:q.exame, acertou, origem});
  const e = ERROS.find(x => x.id === q.id);
  if (!acertou){
    if (e){ e.estagio = 0; e.proxima = addDias(hoje(), 1); e.dominada = false; e.erros = (e.erros || 1) + 1; }
    else ERROS.push({id:q.id, area:i.area || q.especialidade, tema:i.tema || q.tema, adicionado:hoje(), estagio:0, proxima:addDias(hoje(), 1), dominada:false, erros:1});
  } else if (e && origem === 'erros' && !e.dominada){
    e.estagio += 1;
    if (e.estagio >= 3){ e.dominada = true; e.proxima = null; toast('🏆 Questão dominada!'); }
    else e.proxima = addDias(hoje(), e.estagio === 1 ? 7 : 30);
  }
  salvar();
}

/* ============================================================ MISSÃO */
function pesosFraquezas(){
  const porArea = {}, porTema = {};
  HIST.forEach(h => { for (const [m, k] of [[porArea, h.area], [porTema, h.tema]]){ m[k] = m[k] || {a:0, n:0}; m[k].n++; if (h.acertou) m[k].a++; } });
  const pesos = {};
  STATS.especialidades.forEach(a => { const s = porArea[a]; pesos[a] = !s || s.n < 5 ? 1.5 : 0.4 + 3 * (1 - s.a / s.n); });
  Object.entries(porTema).forEach(([t, s]) => { if (s.n >= 3) pesos[t] = 0.3 + 4 * (1 - s.a / s.n); });
  return pesos;
}
let modoMissao = 'equilibrada';
$$('#mModo button').forEach(b => b.onclick = () => {
  modoMissao = b.dataset.m; $$('#mModo button').forEach(x => x.classList.toggle('on', x === b));
  $('#mAreaWrap').hidden = $('#mTemaWrap').hidden = modoMissao !== 'area';
});

function renderMissao(){
  const d = hoje(), m = MISSAO.dias[d];
  $('#mDataHoje').textContent = new Date().toLocaleDateString('pt-BR', {weekday:'long', day:'2-digit', month:'long'});
  const resp = m ? Object.keys(m.respostas).length : 0, tot = m ? m.ids.length : 15;
  $('#mProg').textContent = `${resp}/${tot}`;
  $('#mStreak').textContent = `${streakAtual()} dia${streakAtual() === 1 ? '' : 's'}`;
  const vistos = new Set(MISSAO.vistas);
  $('#mRest').textContent = STATS.total_unicas_validas - [...vistos].filter(id => IDX[id] && IDX[id].valida && !IDX[id].dup).length;
  $('#mRev').textContent = ERROS.filter(e => !e.dominada && e.proxima <= d).length;
  const concl = m && m.concluida;
  $('#mDone').hidden = !concl;
  $('#mConfig').hidden = !!concl && !m.extraEmAndamento;
  $('#mStart').innerHTML = ic('play') + (m && !concl ? `Continuar missão (${resp}/${tot})` : 'Iniciar missão de hoje');
  $('#mInfo').textContent = m && !concl ? `Modo: ${m.modo}. As questões de hoje já foram sorteadas e ficam fixas até meia-noite.` : '';
  if (concl){
    const ac = Object.values(m.respostas).filter(r => r.acertou).length;
    const porArea = {};
    m.ids.forEach(id => { const a = IDX[id]?.area; const r = m.respostas[id]; if (!a || !r) return; porArea[a] = porArea[a] || {a:0, n:0}; porArea[a].n++; if (r.acertou) porArea[a].a++; });
    $('#mDoneResumo').innerHTML = `<p style="font-size:1.1rem"><b>${ac}/${m.ids.length}</b> acertos (${pct(ac, m.ids.length)}%) · 🔥 sequência de <b>${streakAtual()}</b> dia(s). ${m.ids.length - ac} erro(s) enviados às Revisões (revisão amanhã).</p>` +
      barrasH(Object.entries(porArea).map(([a, s]) => ({label:AREA_CURTA[a] || a, value:pct(s.a, s.n), n:s.n})));
  }
}

$('#mStart').onclick = async () => {
  const d = hoje();
  let m = MISSAO.dias[d];
  if (m && m.concluida) return;
  if (!m){
    $('#mStart').disabled = true; $('#mInfo').textContent = 'Sorteando 15 questões inéditas…';
    try {
      const body = {data:d, vistas:MISSAO.vistas, n:15, modo:modoMissao, pesos:modoMissao === 'fraquezas' ? pesosFraquezas() : {}, exame:$('#mExame').value || null,
        especialidade:modoMissao === 'area' ? ($('#mArea').value || null) : null, tema:modoMissao === 'area' ? ($('#mTema').value || null) : null, semente_usuario:semente()};
      const r = await post('/api/missao', body);
      if (!r.items.length){ toast('Nenhuma questão para esse filtro.'); return; }
      if (r.ciclo_reiniciado) toast('Você já viu todas as questões desse filtro — reiniciando o ciclo!');
      m = MISSAO.dias[d] = {ids:r.items.map(q => q.id), respostas:{}, concluida:false, modo:modoMissao};
      MISSAO.vistas = [...new Set([...MISSAO.vistas, ...m.ids])];
      salvar();
      iniciarSessao({tipo:'missao', titulo:'Missão diária', sub:`${fmtData(d)} · modo ${modoMissao}`, questoes:r.items, modo:'treino', onResposta:respMissao, onFim:fimMissao});
    } catch(e){ toast('Erro ao sortear: ' + e.message); }
    finally { $('#mStart').disabled = false; }
  } else {
    const r = {items: await carregarLote(m.ids)};
    const respostas = {}; Object.entries(m.respostas).forEach(([id, v]) => respostas[id] = {sel:v.sel, acertou:v.acertou, gab:v.gab, answer_token:v.answer_token || ''});
    iniciarSessao({tipo:'missao', titulo:'Missão diária', sub:`${fmtData(d)} · modo ${m.modo}`, questoes:r.items, modo:'treino', respostas, onResposta:respMissao, onFim:fimMissao,
      idx:Math.max(0, r.items.findIndex(q => !m.respostas[q.id]))});
  }
};
function semente(){ let s = ls.get('medquest_semente'); if (!s){ s = Math.random().toString(36).slice(2, 10); ls.set('medquest_semente', s); } return s; }
function respMissao(q, sel, acertou, gab, answerToken){
  const m = MISSAO.dias[hoje()]; if (!m) return;
  m.respostas[q.id] = {sel, acertou, gab, answer_token:answerToken || ''};
  if (!m.concluida && m.ids.every(id => m.respostas[id])){
    m.concluida = true;
    const ontem = addDias(hoje(), -1);
    if (MISSAO.ultimoConcluido !== hoje()){ MISSAO.streak = MISSAO.ultimoConcluido === ontem ? MISSAO.streak + 1 : 1; MISSAO.ultimoConcluido = hoje(); }
    toast(`🔥 Missão concluída! Sequência: ${MISSAO.streak} dia(s)`);
  }
  salvar();
}
function fimMissao(){ mostrar('missao'); }
$('#mRevisar').onclick = async () => { const m = MISSAO.dias[hoje()]; const r = {items: await carregarLote(m.ids)};
  const respostas = {}; Object.entries(m.respostas).forEach(([id, v]) => respostas[id] = v);
  iniciarSessao({tipo:'revisao', titulo:'Revisão da missão de hoje', sub:'Clique nas questões para rever a explicação da IA', questoes:r.items, modo:'treino', respostas, onFim:() => mostrar('missao')}); };
$('#mExtra').onclick = async () => {
  const body = {data:hoje() + '-extra-' + Date.now(), vistas:MISSAO.vistas, n:15, modo:modoMissao, pesos:modoMissao === 'fraquezas' ? pesosFraquezas() : {}, exame:$('#mExame').value || null,
    especialidade:modoMissao === 'area' ? ($('#mArea').value || null) : null, tema:modoMissao === 'area' ? ($('#mTema').value || null) : null, semente_usuario:semente()};
  const r = await post('/api/missao', body);
  MISSAO.vistas = [...new Set([...MISSAO.vistas, ...r.items.map(q => q.id)])]; salvar();
  iniciarSessao({tipo:'extra', titulo:'Missão extra', sub:'15 questões inéditas adicionais', questoes:r.items, modo:'treino', onFim:() => mostrar('missao')});
};
$('#mIrErros').onclick = () => { mostrar('erros'); };

/* ============================================================ SIMULADO */
$('#sStart').onclick = async () => {
  const n = +$('#sN').value;
  const body = {n, ordem:$('#sOrdem').value === 'aleatoria' ? 'aleatoria' : 'prova', apenas_novas:$('#sIncl').value === 'novas', vistas:HIST.slice(-1200).map(h=>h.id), seed:String(Date.now())};
  for (const [k, sel] of [['exame', '#sExame'], ['edicao', '#sEdicao'], ['especialidade', '#sArea'], ['tema', '#sTema']]) if ($(sel).value) body[k] = $(sel).value;
  const r = await post('/api/simulado', body);
  let itens = r.items;
  if (!itens.length){ $('#sInfo').textContent = 'Nenhuma questão com esses filtros.'; return; }
  const modo = $('#sModo').value;
  const desc = [$('#sExame').value, $('#sEdicao').value, $('#sArea').value, $('#sTema').value].filter(Boolean).join(' · ') || 'Todas as provas e áreas';
  iniciarSessao({tipo:'simulado', titulo:`Simulado (${itens.length} questões)`, sub:desc + (modo === 'prova' ? ' · modo prova' : ' · modo treino'), questoes:itens, modo, onFim:() => mostrar('simulado')});
};

/* ============================================================ BANCO */
let bancoOffset = 0, bancoTotal = 0;
let bancoReq = 0;
async function buscarBanco(reset = true){
  if (reset){ bancoOffset = 0; $('#bLista').innerHTML = ''; }
  const p = paramsBanco(); p.set('limit', 30); p.set('offset', bancoOffset);
  const tk = ++bancoReq;
  const r = await api('/api/questoes?' + p);
  if (tk !== bancoReq) return;   // resposta de uma busca antiga: descarta
  bancoTotal = r.total; bancoOffset += r.items.length;
  const resp = {}; HIST.forEach(h => resp[h.id] = h.acertou);
  $('#bInfo').textContent = `${r.total} questões encontradas`;
  $('#bLista').insertAdjacentHTML('beforeend', r.items.map(q => itemHtml(q, resp)).join('') || '<div class="empty">Nada encontrado.</div>');
  $('#bMais').hidden = bancoOffset >= bancoTotal;
  ligarItens('#bLista', 'banco');
}
function itemHtml(q, resp){
  const d = q.duplicata_de && IDX[q.duplicata_de];
  return `<div class="list-item" data-id="${q.id}">
      <div class="row small" style="justify-content:space-between"><span><b>${esc(q.edicao)}</b> · Q${q.numero}${favs().includes(q.id) ? ` <span class="red">${ic('star')}</span>` : ''}</span>
      <span class="row" style="gap:4px"><span class="pill pri">${esc(AREA_CURTA[q.especialidade] || q.especialidade)}</span><span class="pill">${esc(q.tema)}</span>
      ${q.id in resp ? (resp[q.id] ? `<span class="pill ok">${ic('check')}acertou</span>` : `<span class="pill bad">${ic('x')}errou</span>`) : ''}${!q.valida ? '<span class="pill warn">anulada</span>' : ''}${d ? `<span class="pill" title="Questão repetida">= ${esc(d.edicao)} Q${d.numero}</span>` : ''}</span></div>
      <div style="margin-top:6px">${esc((q.preview || '').slice(0, 160))}${(q.preview || '').length >= 160 ? '…' : ''}</div>
    </div>`;
}
function ligarItens(cont, volta){
  $$(cont + ' .list-item').forEach(el => el.onclick = async () => { const q = await api('/api/questoes/' + el.dataset.id); iniciarSessao({tipo:'banco', titulo:'Questão do banco', sub:`${q.edicao} · Q${q.numero}`, questoes:[q], modo:'treino', onFim:() => mostrar(volta)}); });
}
function paramsBanco(){ const p = new URLSearchParams(); for (const [k, s] of [['exame', '#bExame'], ['edicao', '#bEdicao'], ['ano', '#bAno'], ['especialidade', '#bArea'], ['tema', '#bTema'], ['q', '#bBusca']]) if ($(s).value) p.set(k, $(s).value); return p; }
$('#bBuscar').onclick = () => buscarBanco(true);
$('#bBusca').onkeydown = e => { if (e.key === 'Enter') buscarBanco(true); };
$('#bMais').onclick = () => buscarBanco(false);
$('#bTreinar').onclick = async () => { const p = paramsBanco(); const body={n:60, ordem:'prova'}; for (const [k,v] of p.entries()) if(['exame','edicao','especialidade','tema'].includes(k)) body[k]=v; const r = await post('/api/simulado', body);
  if (!r.items.length) return toast('Nenhuma questão válida nesse filtro.');
  iniciarSessao({tipo:'banco', titulo:`Treino do banco (${r.items.length})`, sub:'Filtro do banco de questões', questoes:r.items, modo:'treino', onFim:() => mostrar('banco')}); };

/* ============================================================ ERROS */
function errosFiltrados(){
  const a = $('#eArea').value, t = $('#eTema').value, f = $('#eFiltro').value;
  return ERROS.filter(e => (!a || e.area === a) && (!t || e.tema === t) && (f === 'dominadas' ? e.dominada : !e.dominada && (f === 'todos' || e.proxima <= hoje())));
}
$('#eFiltro').onchange = renderErros;
function renderErros(){
  const l = errosFiltrados();
  const est = ['D+1', 'D+7', 'D+30'];
  $('#eInfo').textContent = `${l.length} questão(ões) · pendentes: ${ERROS.filter(e => !e.dominada).length} · dominadas: ${ERROS.filter(e => e.dominada).length}`;
  $('#eLista').innerHTML = l.length ? `<div class="card"><table><tr><th>Questão</th><th>Área / Tema</th><th>Etapa</th><th>Próxima revisão</th><th>Erros</th><th></th></tr>` + l.map(e => {
    const i = IDX[e.id] || {};
    return `<tr><td>${esc(i.edicao || '')} · Q${i.numero || ''}</td><td><span class="pill pri">${esc(AREA_CURTA[e.area] || e.area)}</span> <span class="small">${esc(e.tema)}</span></td>
      <td>${e.dominada ? '<span class="pill ok">dominada</span>' : `<span class="pill warn">${est[e.estagio] || 'D+1'}</span>`}</td>
      <td>${e.dominada ? '—' : (e.proxima <= hoje() ? '<b style="color:var(--bad)">hoje</b>' : fmtData(e.proxima))}</td><td>${e.erros || 1}</td>
      <td><button class="btn sec sm" data-rm="${e.id}" title="Remover do caderno">${ic('trash')}</button></td></tr>`; }).join('') + '</table></div>'
    : '<div class="card empty">🎉 Nenhuma questão aqui. Continue fazendo as missões!</div>';
  $$('[data-rm]').forEach(b => b.onclick = () => { ERROS = ERROS.filter(e => e.id !== +b.dataset.rm); salvar(); renderErros(); });
}
$('#eStart').onclick = async () => {
  const l = errosFiltrados(); if (!l.length) return toast('Nada para revisar com esse filtro.');
  const r = {items: await carregarLote(l.map(e => e.id).slice(0,100))};
  iniciarSessao({tipo:'erros', titulo:`Revisão de erros (${r.items.length})`, sub:'Revisão espaçada D+1 / D+7 / D+30', questoes:r.items, modo:'treino', onFim:() => mostrar('erros')});
};

/* ============================================================ SESSÃO / RENDERIZADOR DE QUESTÃO */
let S = null, timerH = null;
function iniciarSessao(cfg){
  const origem = viewAtual() === 'quiz' ? (S?._origem || 'painel') : viewAtual();
  S = Object.assign({idx:0, respostas:{}, selecao:{}, riscadas:{}, explic:{}, dicas:{}, inicio:Date.now(), finalizado:false, _origem:origem}, cfg);
  if (S.tipo === 'revisao') S.finalizado = true;
  $('#qzTitulo').textContent = S.titulo; $('#qzSub').textContent = S.sub || '';
  clearInterval(timerH); $('#qzTimer').hidden = S.modo !== 'prova';
  if (S.modo === 'prova') timerH = setInterval(() => { const s = Math.floor((Date.now() - S.inicio) / 1000); $('#qzTimer').textContent = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`; }, 1000);
  mostrar('quiz');
  renderQuestao();
}
$('#qzSair').onclick = () => {
  if (!confirmarSaidaProva()) return;
  const destino = S?._origem || 'painel';
  clearInterval(timerH);
  S = null;
  if (history.state?.cti && history.state.view === 'quiz' && history.length > 1) history.back();
  else mostrar(destino, {substituir:true});
};

function renderGrid(){
  $('#qzGrid').innerHTML = S.questoes.map((q, i) => { const r = S.respostas[q.id]; let c = '';
    if (r) c = r.acertou ? 'ok' : 'bad'; else if (S.selecao[q.id]) c = 'ans';
    return `<button class="${c} ${i === S.idx ? 'cur' : ''}" data-i="${i}">${i + 1}</button>`; }).join('');
  $$('#qzGrid button').forEach(b => b.onclick = () => { S.idx = +b.dataset.i; renderQuestao(); });
  const feitas = S.questoes.filter(q => S.respostas[q.id] || (S.modo === 'prova' && S.selecao[q.id])).length;
  $('#qzBar').style.width = pct(feitas, S.questoes.length) + '%';
}

function renderQuestao(){
  if (S.idx >= S.questoes.length) return renderResultado();
  const q = S.questoes[S.idx], r = S.respostas[q.id], sel = r ? r.sel : S.selecao[q.id];
  const respondida = !!r, prova = S.modo === 'prova' && !S.finalizado;
  const alts = LETRAS.filter(l => q['alt_' + l]);
  const riscadas = S.riscadas[q.id] || {};
  renderGrid();
  $('#qzCard').innerHTML = `
    <div class="q-top"><div class="row" style="gap:6px"><b>Questão ${S.idx + 1}/${S.questoes.length}</b><span class="pill">${esc(q.edicao)} · Q${q.numero}</span></div>
      <div class="row" style="gap:6px"><span class="pill pri">${esc(q.especialidade)}</span><span class="pill pur">${esc(q.tema)}</span>${favBtn(q.id)}</div></div>
    ${q.tem_imagem ? '<div class="imgwarn">' + ic('img') + ' Na prova original esta questão tem imagem/figura (ECG, foto, partograma…) que não pôde ser extraída do PDF. Resolva pelo texto; a imagem pode estar no caderno oficial do INEP.</div>' : ''}
    <div class="q-enun">${esc(q.enunciado)}</div>
    <div id="qzAlts">${alts.map(l => {
      let cls = '';
      if (respondida){ if (l === r.gab || r.gab === 'ANULADA') cls = 'certa'; else if (l === r.sel) cls = 'errada'; }
      else if (l === sel) cls = 'sel';
      if (riscadas[l]) cls += ' riscada';
      return `<button class="alt ${cls}" data-l="${l}" ${respondida ? 'disabled' : ''}><span class="lt">${l}</span><span class="tx">${esc(q['alt_' + l])}</span>${!respondida ? `<span class="risk" data-r="${l}" title="Riscar alternativa">✂︎</span>` : ''}</button>`; }).join('')}</div>
    <div id="qzFb">${respondida ? feedbackHtml(r) : ''}</div>
    <div id="qzDica">${S.dicas[q.id] ? dicaHtml(S.dicas[q.id]) : ''}</div>
    <div class="nav-q">
      <div class="row"><button class="btn sec" id="qzPrev" ${S.idx === 0 ? 'disabled' : ''}>← Anterior</button>
        ${!respondida && !prova ? `<button class="btn sec" id="qzDicaBtn" style="color:var(--red);background:var(--redl);border-color:var(--red)">${ic('bulb')}Dica da IA (sem spoiler)</button>` : ''}</div>
      <div class="row">
        ${!respondida && !prova ? `<button class="btn" id="qzConf" ${sel ? '' : 'disabled'}>Confirmar resposta</button>` : ''}
        ${prova ? `<button class="btn ${S.idx === S.questoes.length - 1 ? 'ok' : 'sec'}" id="qzFinal">${ic('flag')}Finalizar e corrigir</button>` : ''}
        <button class="btn ${respondida ? '' : 'sec'}" id="qzNext">${S.idx === S.questoes.length - 1 ? (prova ? 'Revisar' : 'Ver resultado →') : 'Próxima →'}</button>
      </div>
    </div>
    <div class="small muted" style="margin-top:8px">Atalhos: A–E seleciona · Enter confirma · → próxima · ← anterior</div>
    <div id="qzIA">${respondida && S.explic[q.id] ? iaHtml(S.explic[q.id], r) : ''}</div>`;
  $$('#qzAlts .alt').forEach(b => b.onclick = e => {
    if (e.target.dataset.r){ e.stopPropagation(); const l = e.target.dataset.r; S.riscadas[q.id] = Object.assign({}, riscadas, {[l]:!riscadas[l]}); return renderQuestao(); }
    if (respondida) return; S.selecao[q.id] = b.dataset.l; renderQuestao(); });
  $('#qzPrev').onclick = () => { S.idx--; renderQuestao(); };
  $('#qzNext').onclick = () => { if (prova && S.idx === S.questoes.length - 1) { S.idx = 0; return renderQuestao(); } S.idx++; renderQuestao(); };
  $('#qzConf') && ($('#qzConf').onclick = () => confirmar(q));
  $('#qzDicaBtn') && ($('#qzDicaBtn').onclick = async () => {
    $('#qzDica').innerHTML = `<div class="dica small muted">${ic('bulb')} Buscando a dica…</div>`;
    const c = iaCache(q.id);
    let d; try { d = c && c.dica ? {fonte:'ia', dicas:[c.dica]} : await api('/api/ia-dica/' + q.id, {headers:{'X-Question-Token':q._access || ''}}); } catch(e){ d = {fonte:'indisponivel', dicas:[]}; }
    S.dicas[q.id] = d; $('#qzDica').innerHTML = dicaHtml(d); });
  $('#qzFinal') && ($('#qzFinal').onclick = finalizarProva);
  ligarFav('#qzCard');
  if (respondida && !S.explic[q.id]) carregarIA(q, r);
}
function feedbackHtml(r){
  if (r.gab === 'ANULADA') return `<div class="fb warn">${ic('alert')}Questão ANULADA pela banca — conta como acerto. Use como revisão.</div>`;
  return r.acertou ? `<div class="fb ok">${ic('check')}Correto! Gabarito oficial: ${r.gab}</div>` : `<div class="fb bad">${ic('x')}Você marcou ${r.sel || '—'}. Gabarito oficial: ${r.gab}${S.tipo !== 'revisao' ? ' · enviada às Revisões' : ''}</div>`;
}
async function confirmar(q){
  const sel = S.selecao[q.id]; if (!sel) return;
  $('#qzConf').disabled = true;
  try {
    const r = await post('/api/responder', {question_id:q.id, selected_option:sel, access_token:q._access || ''});
    S.respostas[q.id] = {sel, acertou:r.is_correct, gab:r.gabarito_oficial, answer_token:r.answer_token || ''};
    S.explic[q.id] = r.explicacao_ia || iaCache(q.id) || undefined; if (r.explicacao_ia) iaGuardar(q.id, r.explicacao_ia);
    registrar(q, r.is_correct, S.tipo);
    S.onResposta && S.onResposta(q, sel, r.is_correct, r.gabarito_oficial, r.answer_token || '');
    renderQuestao();
    setTimeout(() => $('#qzIA')?.scrollIntoView({behavior:'smooth', block:'start'}), 80);
  } catch(e){ toast('Erro: ' + e.message); $('#qzConf').disabled = false; }
}
/* ---------- IA: comentário salvo no aparelho → servidor (salvo → Gemini → fallback Gemini → NVIDIA) → "indisponível" */
async function buscarIA(id, qToken='', answerToken=''){
  if (IAC[id]) return IAC[id];
  let x; try { x = await api('/api/ia-explicar/' + id, {headers:{'X-Question-Token':qToken || '', 'X-Answer-Token':answerToken || ''}}); } catch(e){ x = {fonte:'indisponivel', motivo:e.message}; }
  iaGuardar(id, x); return x;
}
function iaIndispHtml(x, id){
  return `<div class="ia"><b class="row" style="gap:6px">${ic('ai')}IA Tutora</b>
    <div class="imgwarn" style="margin-top:8px">${ic('alert')} O comentário da IA não está disponível agora. Pode ser o limite diário do plano grátis ou falta de conexão. O gabarito acima é o oficial.</div>
    <div class="row" style="justify-content:space-between;margin-top:8px"><span class="tiny muted">${esc(x.motivo || '')}</span><button class="btn sec sm" data-ia-retry="${id}">${ic('ai')}Tentar de novo</button></div></div>`;
}
async function carregarIA(q, r){
  $('#qzIA').innerHTML = `<div class="ia"><b class="row" style="gap:6px">${ic('ai')}IA Tutora</b> <span class="muted small">analisando a questão… (a primeira vez pode levar alguns segundos; depois fica salvo)</span></div>`;
  const x = await buscarIA(q.id, q._access || '', r.answer_token || '');
  if (!(S && S.questoes[S.idx]?.id === q.id)) return;
  if (x.fonte === 'ia'){ S.explic[q.id] = x; $('#qzIA').innerHTML = iaHtml(x, r); }
  else { $('#qzIA').innerHTML = iaIndispHtml(x, q.id); $('#qzIA [data-ia-retry]').onclick = () => carregarIA(q, r); }
}
function dicaHtml(d){ if (d.fonte === 'indisponivel' || !d.dicas?.length) return `<div class="dica small">${ic('bulb')} Dica indisponível agora (limite da IA ou sem conexão). Tente de novo mais tarde.</div>`;
  return `<div class="dica"><b>${ic('bulb')} Dica da IA</b> <span class="tiny muted">(não revela a resposta)</span><ul>${d.dicas.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>`; }
function iaHtml(x, r){
  if (!x || x.fonte !== 'ia') return '';
  const alerta = x.alerta_revisao ? x.alerta_revisao.replace(/^POSSÍVEL INCONSISTÊNCIA DE GABARITO:\s*/i, '') : '';
  return `<div class="ia">
    <div class="row" style="justify-content:space-between"><b class="row" style="gap:6px">${ic('ai')}IA Tutora · revisão da questão</b><span class="tiny muted">${esc(x.modelo || 'IA')}</span></div>
    ${x.alerta_revisao ? `<div class="imgwarn">${ic('flag')} <b>${/^POSSÍVEL INCONSISTÊNCIA/i.test(x.alerta_revisao) ? 'Possível inconsistência de gabarito' : 'Atenção'}:</b> ${esc(alerta)} O gabarito exibido é o oficial da banca.</div>` : ''}
    <h4>${ic('check')}${esc(x.resumo)}</h4>
    ${x.achado_chave || x.conceito_cobrado ? `<div class="row small" style="gap:6px;flex-wrap:wrap;margin:6px 0">${x.achado_chave ? `<span class="pill pri" style="white-space:normal">${ic('search')}Achado-chave: ${esc(x.achado_chave)}</span>` : ''}${x.conceito_cobrado ? `<span class="pill" style="white-space:normal">${ic('target')}Conceito cobrado: ${esc(x.conceito_cobrado)}</span>` : ''}</div>` : ''}
    ${x.porque_correta ? `<div>${esc(x.porque_correta)}</div>` : ''}
    <h4>${ic('search')}Alternativa por alternativa</h4>
    ${x.alternativas.map(a => `<div class="alt-an ${a.correta ? 'c' : 'w'}"><b>${a.letra}) ${a.correta ? '✔' : '✘'}</b> <span class="muted">${esc(a.texto.slice(0, 140))}${a.texto.length > 140 ? '…' : ''}</span>${a.analise ? '<br>' + esc(a.analise) : ''}</div>`).join('')}
    ${x.pontos_atencao?.length ? `<h4>${ic('alert')}Pegadinhas da banca</h4><ul>${x.pontos_atencao.map(p => `<li>${esc(p)}</li>`).join('')}</ul>` : ''}
    ${x.bizu ? `<h4>${ic('target')}Resumo para memorizar</h4><div class="bizu">${esc(x.bizu)}</div>` : ''}
    ${x.foco?.length ? `<h4>${ic('pin')}Foco no que importa · ${esc(x.tema)}</h4><ul>${x.foco.map(p => `<li>${esc(p)}</li>`).join('')}</ul>` : ''}
    <div class="ia-footer small muted"><span class="ia-ref">${x.referencia ? ic('book') + ' ' + esc(x.referencia) : ''}</span>
      <button class="btn sec sm" data-open-reforco="${esc(x.tema)}">${ic('trilha')}Revisar este tema na Trilha</button></div>
  </div>`;
}
async function finalizarProva(){
  const faltam = S.questoes.filter(q => !S.selecao[q.id]).length;
  if (faltam && !confirm(`Há ${faltam} questão(ões) em branco. Finalizar mesmo assim?`)) return;
  clearInterval(timerH);
  const r = await post('/api/responder-lote', {answers:S.questoes.map(q => ({question_id:q.id, selected_option:S.selecao[q.id] || null, access_token:q._access || ''}))});
  const g = r.items || {};
  S.questoes.forEach(q => { const sel = S.selecao[q.id] || null, z = g[q.id] || g[String(q.id)]; if (!z) return; const gab = z.gabarito_oficial; const ac = z.is_correct;
    S.respostas[q.id] = {sel, acertou:ac, gab, answer_token:z.answer_token || ''}; registrar(q, ac, S.tipo); });
  S.finalizado = true; S.idx = S.questoes.length; renderQuestao();
}
function renderResultado(){
  renderGrid();
  const tot = S.questoes.length, resp = S.questoes.filter(q => S.respostas[q.id]);
  const ac = resp.filter(q => S.respostas[q.id].acertou).length;
  const porArea = {};
  resp.forEach(q => { const a = q.especialidade; porArea[a] = porArea[a] || {a:0, n:0}; porArea[a].n++; if (S.respostas[q.id].acertou) porArea[a].a++; });
  const tempo = Math.round((Date.now() - S.inicio) / 60000);
  if (S.tipo === 'simulado' && !S.registrado && resp.length){ S.registrado = true; const l = ls.get('cti_simulados', []); l.push({data:hoje(), n:tot, ac}); ls.set('cti_simulados', l); }
  $('#qzCard').innerHTML = `<h2>${ic('trophy')}Resultado</h2>
    <div class="grid g4"><div class="kpi"><span class="small">Acertos</span><b style="color:${corAcerto(pct(ac, resp.length))}">${ac}/${resp.length}</b></div>
      <div class="kpi"><span class="small">Aproveitamento</span><b>${pct(ac, resp.length)}%</b></div><div class="kpi"><span class="small">Não respondidas</span><b>${tot - resp.length}</b></div>
      <div class="kpi"><span class="small">Tempo</span><b>${tempo} min</b></div></div>
    <h3 style="margin-top:16px">Por área</h3>${barrasH(Object.entries(porArea).map(([a, s]) => ({label:AREA_CURTA[a] || a, value:pct(s.a, s.n), n:s.n})))}
    <p class="small muted">Clique nos números acima para rever cada questão com a explicação completa da IA. Os erros já estão nas Revisões.</p>
    <div class="row"><button class="btn" id="rsRev">${ic('search')}Revisar desde a 1ª</button><button class="btn sec" id="rsSair">Concluir</button></div>`;
  $('#rsRev').onclick = () => { S.idx = 0; renderQuestao(); };
  $('#rsSair').onclick = () => $('#qzSair').click();
}
function teclas(e){
  if (!S || !$('#view-quiz').classList.contains('on') || /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
  const k = e.key.toUpperCase(), q = S.questoes[S.idx];
  if (q && LETRAS.includes(k) && q['alt_' + k] && !S.respostas[q.id]){ S.selecao[q.id] = k; renderQuestao(); }
  else if (e.key === 'Enter' && $('#qzConf') && !$('#qzConf').disabled) confirmar(q);
  else if (e.key === 'ArrowRight' && $('#qzNext')) $('#qzNext').click();
  else if (e.key === 'ArrowLeft' && $('#qzPrev') && !$('#qzPrev').disabled) $('#qzPrev').click();
}

/* ============================================================ GRÁFICOS SVG */
function barrasH(dados){
  if (!dados.length) return '<div class="empty small">Sem dados ainda.</div>';
  const h = 30, W = 560, L = 110;
  return `<svg viewBox="0 0 ${W} ${dados.length * h + 10}" width="100%" role="img">${dados.map((d, i) => { const w = (W - L - 90) * d.value / 100, y = i * h + 5;
    return `<text x="0" y="${y + 18}" font-size="13" fill="currentColor">${esc(d.label)}</text>
      <rect x="${L}" y="${y + 4}" width="${W - L - 90}" height="18" rx="5" fill="var(--card2)"/>
      <rect x="${L}" y="${y + 4}" width="${Math.max(2, w)}" height="18" rx="5" fill="${corAcerto(d.value)}"/>
      <text x="${W - 84}" y="${y + 18}" font-size="12.5" font-weight="700" fill="currentColor">${d.value}% <tspan fill="var(--mut)" font-weight="400">(${d.n})</tspan></text>`; }).join('')}
    <line x1="${L + (W - L - 90) * .6}" x2="${L + (W - L - 90) * .6}" y1="0" y2="${dados.length * h + 8}" stroke="var(--warn)" stroke-dasharray="4 3"/></svg>
    <div class="tiny muted">Linha tracejada = meta de 60% (nota de corte típica do Revalida). Verde ≥ 70% · Amarelo 50–69% · Vermelho &lt; 50%.</div>`;
}
function barrasV(dados, alt = 180){
  if (!dados.some(d => d.n)) return '<div class="empty small">Responda questões para ver sua evolução.</div>';
  const W = Math.max(420, dados.length * 44), bw = Math.min(30, (W - 40) / dados.length - 8), top = 18;
  return `<svg viewBox="0 0 ${W} ${alt + 40}" width="100%" role="img">
    ${[0, 50, 100].map(v => `<line x1="30" x2="${W}" y1="${top + alt - alt * v / 100}" y2="${top + alt - alt * v / 100}" stroke="var(--bd)"/><text x="0" y="${top + alt - alt * v / 100 + 4}" font-size="10" fill="var(--mut)">${v}%</text>`).join('')}
    <line x1="30" x2="${W}" y1="${top + alt * .4}" y2="${top + alt * .4}" stroke="var(--warn)" stroke-dasharray="4 3"/>
    ${dados.map((d, i) => { const x = 38 + i * ((W - 40) / dados.length), hh = alt * d.value / 100;
      return `${d.n ? `<rect x="${x}" y="${top + alt - hh}" width="${bw}" height="${Math.max(1, hh)}" rx="4" fill="${corAcerto(d.value)}"><title>${d.label}: ${d.value}% (${d.n} questões)</title></rect>
        <text x="${x + bw / 2}" y="${top + alt - hh - 4}" font-size="10" text-anchor="middle" fill="currentColor">${d.value}</text>` : ''}
        <text x="${x + bw / 2}" y="${top + alt + 14}" font-size="9.5" text-anchor="middle" fill="var(--mut)">${esc(d.label)}</text>
        <text x="${x + bw / 2}" y="${top + alt + 26}" font-size="9" text-anchor="middle" fill="var(--lt)">${d.n ? d.n + 'q' : ''}</text>`; }).join('')}</svg>`;
}
function barrasAgrupadas(grupos, series){
  const cores = ['#CBD5E1', '#94A3B8', '#475569', '#E10613'];
  const W = 640, alt = 170, top = 14, gw = (W - 40) / grupos.length, bw = Math.min(22, gw / (series + 1));
  return `<svg viewBox="0 0 ${W} ${alt + 50}" width="100%">
    ${[0, 50, 100].map(v => `<line x1="30" x2="${W}" y1="${top + alt - alt * v / 100}" y2="${top + alt - alt * v / 100}" stroke="var(--bd)"/><text x="0" y="${top + alt - alt * v / 100 + 4}" font-size="10" fill="var(--mut)">${v}%</text>`).join('')}
    ${grupos.map((g, i) => g.valores.map((v, j) => { const x = 36 + i * gw + j * (bw + 3), hh = v.n ? alt * v.p / 100 : 0;
      return v.n ? `<rect x="${x}" y="${top + alt - hh}" width="${bw}" height="${Math.max(1, hh)}" rx="3" fill="${cores[j]}"><title>${g.label} – ${v.rot}: ${v.p}% (${v.n}q)</title></rect>` : `<rect x="${x}" y="${top + alt - 2}" width="${bw}" height="2" fill="var(--bd)"/>`; }).join('') +
      `<text x="${36 + i * gw + (series * (bw + 3)) / 2}" y="${top + alt + 16}" font-size="11" text-anchor="middle" fill="currentColor">${esc(g.label)}</text>`).join('')}
    ${['3 sem. atrás', '2 sem. atrás', 'semana passada', 'esta semana'].map((r, j) => `<rect x="${40 + j * 150}" y="${alt + 34}" width="10" height="10" fill="${cores[j]}"/><text x="${54 + j * 150}" y="${alt + 43}" font-size="10.5" fill="var(--mut)">${r}</text>`).join('')}</svg>`;
}

/* ============================================================ DESEMPENHO */
function agrega(lista, chave){ const m = {}; lista.forEach(h => { const k = h[chave]; m[k] = m[k] || {a:0, n:0}; m[k].n++; if (h.acertou) m[k].a++; }); return m; }
function renderDesempenho(){
  const tot = HIST.length, ac = HIST.filter(h => h.acertou).length;
  const dias = new Set(HIST.map(h => h.data)).size;
  const ult7 = HIST.filter(h => h.data >= addDias(hoje(), -6));
  $('#dKpis').innerHTML = [
    ['Questões respondidas', tot], ['Aproveitamento geral', pct(ac, tot) + '%'], ['Últimos 7 dias', `${ult7.length}q · ${pct(ult7.filter(h => h.acertou).length, ult7.length)}%`],
    ['Sequência de missões', `${streakAtual()} dia(s)`], ['Dias estudados', dias], ['Erros pendentes', ERROS.filter(e => !e.dominada).length]
  ].map(([l, v]) => `<div class="card kpi" style="margin:0"><span class="small muted">${l}</span><b>${v}</b></div>`).join('');
  const porArea = agrega(HIST, 'area');
  $('#dAreas').innerHTML = barrasH(STATS.especialidades.map(a => ({label:AREA_CURTA[a] || a, value:pct(porArea[a]?.a || 0, porArea[a]?.n || 0), n:porArea[a]?.n || 0})));
  // alertas
  const porTema = agrega(HIST, 'tema');
  const al = [];
  STATS.especialidades.forEach(a => { const s = porArea[a]; if (!s) return; const p = pct(s.a, s.n);
    if (s.n >= 5 && p < 50) al.push({c:'bad', t:`<b>${a}: ${p}%</b> em ${s.n} questões — <b>precisa melhorar</b>. Está abaixo da nota de corte.`, a});
    else if (s.n >= 5 && p < 60) al.push({c:'warn', t:`<b>${a}: ${p}%</b> em ${s.n} questões — atenção, perto do limite de 60%.`, a});
    else if (s.n >= 10 && p >= 75) al.push({c:'ok', t:`<b>${a}: ${p}%</b> — ponto forte. Mantenha com revisões.`, a}); });
  Object.entries(porTema).filter(([, s]) => s.n >= 3 && pct(s.a, s.n) < 50).sort((x, y) => pct(x[1].a, x[1].n) - pct(y[1].a, y[1].n)).slice(0, 4)
    .forEach(([t, s]) => al.push({c:'bad', t:`Tema <b>${t}</b>: ${pct(s.a, s.n)}% (${s.n}q). Revise o conteúdo e treine o tema.`, tema:t}));
  const nunca = STATS.especialidades.filter(a => !porArea[a]);
  if (nunca.length && tot) al.push({c:'warn', t:`Você ainda não respondeu questões de: <b>${nunca.join(', ')}</b>.`});
  $('#dAlertas').innerHTML = al.length ? al.map(x => `<div class="alert ${x.c}"><div style="flex:1">${x.t}</div>${x.a || x.tema ? `<div class="row" style="gap:6px">
      <button class="btn sm" ${x.a ? `data-train-area="${esc(x.a)}"` : `data-train-tema="${esc(x.tema)}"`}>${ic('play')}Treinar</button>
      <button class="btn sec sm" ${x.tema ? `data-open-reforco="${esc(x.tema)}"` : `data-open-reforco-area="${esc(x.a)}"`}>${ic('trilha')}Trilha</button></div>` : ''}</div>`).join('')
    : `<div class="empty small">${tot ? 'Tudo equilibrado até aqui — continue! 💪' : 'Faça sua primeira missão diária para a IA analisar seu desempenho.'}</div>`;
  renderEvolucao();
  // semanal por área
  const semanas = [3, 2, 1, 0].map(k => [addDias(hoje(), -7 * k - 6), addDias(hoje(), -7 * k)]);
  $('#dSemArea').innerHTML = HIST.length ? barrasAgrupadas(STATS.especialidades.map(a => ({label:AREA_CURTA[a] || a, valores:semanas.map(([i, f], j) => {
    const l = HIST.filter(h => h.area === a && h.data >= i && h.data <= f); return {n:l.length, p:pct(l.filter(h => h.acertou).length, l.length), rot:['3 sem. atrás', '2 sem. atrás', 'semana passada', 'esta semana'][j]}; })})), 4)
    : '<div class="empty small">Sem dados ainda.</div>';
  // temas
  const temas = Object.entries(porTema).sort((x, y) => pct(x[1].a, x[1].n) - pct(y[1].a, y[1].n));
  $('#dTemas').innerHTML = temas.length ? `<table><tr><th>Tema</th><th>Área</th><th>Acerto</th><th>Questões</th><th></th></tr>${temas.map(([t, s]) => { const p = pct(s.a, s.n), area = HIST.find(h => h.tema === t)?.area || '';
    return `<tr><td>${esc(t)}</td><td class="small">${esc(AREA_CURTA[area] || area)}</td><td><b style="color:${corAcerto(p)}">${p}%</b>${s.n < 3 ? ' <span class="tiny muted">(poucos dados)</span>' : ''}</td><td>${s.n}</td>
      <td><button class="btn sec sm" data-train-tema="${esc(t)}">Treinar</button></td></tr>`; }).join('')}</table>` : '<div class="empty small">Sem dados ainda.</div>';
}
function renderEvolucao(){
  const a = $('#dEvArea').value, per = $('#dEvPer').value;
  const l = a ? HIST.filter(h => h.area === a) : HIST;
  let dados;
  if (per === 'dia') dados = Array.from({length:14}, (_, i) => { const d = addDias(hoje(), i - 13); const x = l.filter(h => h.data === d); return {label:d.slice(8) + '/' + d.slice(5, 7), n:x.length, value:pct(x.filter(h => h.acertou).length, x.length)}; });
  else dados = Array.from({length:8}, (_, i) => { const k = 7 - i, ini = addDias(hoje(), -7 * k - 6), fim = addDias(hoje(), -7 * k); const x = l.filter(h => h.data >= ini && h.data <= fim);
    return {label:k ? `-${k}sem` : 'atual', n:x.length, value:pct(x.filter(h => h.acertou).length, x.length)}; });
  $('#dEvol').innerHTML = barrasV(dados);
}
$('#dEvArea').onchange = renderEvolucao; $('#dEvPer').onchange = renderEvolucao;
window.treinarArea = (area, tema) => { mostrar('simulado'); $('#sArea').value = area || (tema ? INDICE.find(i => i.tema === tema)?.area : '') || ''; $('#sArea').onchange(); $('#sTema').value = tema || ''; $('#sModo').value = 'treino'; $('#sN').value = '10'; $('#sStart').click(); };
const CHAVES_APP = k => k.startsWith('medquest_') || k.startsWith('cti_');
$('#dExport').onclick = () => {   // backup completo: histórico, revisões, missões, planner, favoritos, metas, perfil, simulados…
  const d = {_cti_backup:1, _data:new Date().toISOString()};
  for (let i = 0; i < localStorage.length; i++){ const k = localStorage.key(i); if (CHAVES_APP(k)) d[k] = ls.get(k, null); }
  const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify(d)], {type:'application/json'}));
  a.download = `cti_progresso_${hoje()}.json`; a.click(); toast('Backup baixado. Importe no outro aparelho em Desempenho → Dados.'); };
$('#dImport').onchange = async e => { try {
    const d = JSON.parse(await e.target.files[0].text());
    const ks = Object.keys(d).filter(CHAVES_APP); if (!ks.length) throw 0;
    if (!confirm('Substituir o progresso deste aparelho pelo do arquivo?')) return;
    ks.forEach(k => ls.set(k, d[k])); toast('Progresso importado!'); setTimeout(() => location.reload(), 600);
  } catch { toast('Arquivo inválido'); } };
$('#dReset').onclick = () => { if (!confirm('Apagar TODO o histórico, erros, missões e metas?')) return; ls.set('cti_simulados', []); HIST = []; ERROS = []; MISSAO = {streak:0, ultimoConcluido:null, vistas:[], dias:{}}; salvar(); renderDesempenho(); };

/* ============================================================ REFORÇO & PLANNER */
async function renderReforco(){
  if (!REFORCO) REFORCO = await api('/api/reforco');
  const prova = ls.get('medquest_prova', ''); $('#pProva').value = prova;
  $('#pHoras').value = ls.get('medquest_horas', '2');
  renderPlanner(); renderTemas();
}
$('#pProva').onchange = () => { ls.set('medquest_prova', $('#pProva').value); renderPlanner(); };
$('#pHoras').onchange = () => { ls.set('medquest_horas', $('#pHoras').value); renderPlanner(); };
$('#rArea').onchange = renderTemas; $('#rBusca').oninput = renderTemas;
function temasFracos(){
  const porTema = agrega(HIST, 'tema');
  const pool = STATS.pool_por_tema;
  const lista = Object.keys(REFORCO.temas).map(t => { const s = porTema[t]; return {t, area:REFORCO.temas[t].area, p:s ? pct(s.a, s.n) : null, n:s?.n || 0, peso:pool[t] || 0}; });
  // prioridade: temas com dados e acerto baixo; depois temas nunca vistos com muitas questões nas provas
  return lista.sort((a, b) => { const sa = a.n >= 3 ? a.p : 65 - a.peso / 10, sb = b.n >= 3 ? b.p : 65 - b.peso / 10; return sa - sb; });
}
function renderPlanner(){
  const horas = +$('#pHoras').value, prova = $('#pProva').value;
  if (prova){ const d = Math.ceil((new Date(prova + 'T12:00:00') - new Date(hoje() + 'T12:00:00')) / 864e5);
    $('#pCountdown').innerHTML = d >= 0 ? `<span class="small muted">Faltam</span><b>${d} dias</b><span class="tiny muted">≈ ${d * 15} questões de missão até a prova</span>` : '<span class="small muted">Prova já passou</span>'; }
  else $('#pCountdown').innerHTML = '<span class="small muted">Defina a data da prova para ver a contagem regressiva.</span>';
  const fracos = temasFracos();
  const feito = ls.get('medquest_planner', {});
  let k = 0;
  $('#pDias').innerHTML = Array.from({length:7}, (_, i) => {
    const d = addDias(hoje(), i), dow = new Date(d + 'T12:00:00').getDay();
    const revisoes = ERROS.filter(e => !e.dominada && (i === 0 ? e.proxima <= d : e.proxima === d)).length;
    const tarefas = [];
    tarefas.push(`${ic('target')} Missão diária: 15 questões (~35 min)`);
    if (revisoes) tarefas.push(`${ic('rev')} Revisar ${revisoes} erro(s) agendado(s) (${i === 0 ? 'hoje' : 'D+1/D+7/D+30'})`);
    if (dow === 0) tarefas.push(`${ic('simulado')} Simulado semanal: ${horas >= 3 ? 50 : 30} questões mistas em modo prova`);
    const nTemas = dow === 0 ? (horas >= 2 ? 1 : 0) : (horas >= 3 ? 2 : 1);
    for (let j = 0; j < nTemas; j++){ const t = fracos[k++ % fracos.length]; tarefas.push(`${ic('trilha')} Reforço: <a href="#" data-open-reforco="${esc(t.t)}">${esc(t.t)}</a>: ler pontos-chave + ${horas >= 2 ? 15 : 10} questões do tema${t.n >= 3 ? ` (você: ${t.p}%)` : ''}`); }
    if (horas >= 4 && dow !== 0) tarefas.push(ic('book') + ' Leitura da diretriz de referência do tema mais fraco (30 min)');
    return `<div class="day ${i === 0 ? 'hoje' : ''}"><b>${i === 0 ? 'Hoje' : fmtData(d)}</b>${tarefas.map((t, j) => { const key = d + '|' + j;
      return `<label class="task ${feito[key] ? 'done' : ''}"><input type="checkbox" data-k="${key}" ${feito[key] ? 'checked' : ''}><span>${t}</span></label>`; }).join('')}</div>`; }).join('');
  $$('#pDias input[type=checkbox]').forEach(c => c.onchange = () => { const f = ls.get('medquest_planner', {}); f[c.dataset.k] = c.checked; ls.set('medquest_planner', f); c.parentElement.classList.toggle('done', c.checked); });
}
function renderTemas(){
  const a = $('#rArea').value, b = $('#rBusca').value.toLowerCase();
  const ordem = temasFracos().map(x => x.t);
  const stat = Object.fromEntries(temasFracos().map(x => [x.t, x]));
  const temas = ordem.filter(t => (!a || REFORCO.temas[t].area === a) && (!b || JSON.stringify(REFORCO.temas[t]).toLowerCase().includes(b) || t.toLowerCase().includes(b)));
  $('#rTemas').innerHTML = temas.map(t => { const c = REFORCO.temas[t], s = stat[t];
    const pill = s.n >= 3 ? `<span class="pill ${s.p >= 70 ? 'ok' : s.p >= 60 ? 'warn' : 'bad'}">${s.p}% · ${s.n}q</span>` : '<span class="pill">sem dados</span>';
    return `<div class="card tema-card" id="tema-${encodeURIComponent(t)}" style="margin:0;${s.n >= 3 && s.p < 60 ? 'border-color:var(--bad)' : ''}">
      <h3><span>${esc(t)}</span>${pill}</h3><div class="small muted">${esc(c.area)} · ${c.questoes_disponiveis} questões no banco · ${esc(c.referencia)}</div>
      <b class="small row" style="gap:5px;color:var(--red)">${ic('pin')}O que mais cai</b><ul>${c.pontos.map(p => `<li>${esc(p)}</li>`).join('')}</ul>
      <b class="small row" style="gap:5px;color:var(--red)">${ic('target')}Bizus</b><ul>${c.bizus.map(p => `<li>${esc(p)}</li>`).join('')}</ul>
      <b class="small row" style="gap:5px;color:var(--red)">${ic('alert')}Pegadinhas</b><ul>${c.pegadinhas.map(p => `<li>${esc(p)}</li>`).join('')}</ul>
      <div class="row"><button class="btn sm" data-train-tema="${esc(t)}">${ic('play')}Treinar este tema</button></div></div>`; }).join('') || '<div class="empty">Nenhum tema encontrado.</div>';
  $('#rTemas').insertAdjacentHTML('afterbegin', a ? `<div class="card" style="margin:0;grid-column:1/-1"><b>Estratégia para ${esc(a)}:</b> ${esc(REFORCO.estrategia_por_area[a] || '')}</div>` : '');
}
window.abrirReforco = async (tema, area) => {
  mostrar('reforco'); if (!REFORCO) REFORCO = await api('/api/reforco');
  $('#rArea').value = area || (tema && REFORCO.temas[tema] ? REFORCO.temas[tema].area : ''); $('#rBusca').value = ''; renderTemas();
  if (tema) setTimeout(() => { const el = document.getElementById('tema-' + encodeURIComponent(tema)); if (el){ el.scrollIntoView({behavior:'smooth', block:'center'}); el.style.boxShadow = '0 0 0 3px var(--red)'; } }, 150);
};

/* ============================================================ 2ª FASE – estações práticas (PEP oficial do INEP) */
// cti_2fase: {idEstacao: [{data, nota, seg}]}
let F2 = ls.get('cti_2fase', {});
let F2L = null;                                  // lista resumida vinda de /api/estacoes
const F2S = {est:null, fase:'', t0:0, restante:600, h:null, marc:{}};
const F2_AREAS = ['Clínica Médica', 'Cirurgia', 'Pediatria', 'Ginecologia e Obstetrícia', 'Medicina de Família e Comunidade'];
const F2_ICON = {'Clínica Médica':'steth', 'Cirurgia':'scalpel', 'Pediatria':'baby', 'Ginecologia e Obstetrícia':'venus', 'Medicina de Família e Comunidade':'shield'};
const f2Melhor = id => { const l = F2[id] || []; return l.length ? Math.max(...l.map(x => x.nota)) : null; };
const f2Ultima = id => { const l = F2[id] || []; return l.length ? l[l.length - 1].nota : null; };
const f2Cor = n => n >= 7 ? 'var(--ok)' : n >= 5 ? 'var(--warn)' : 'var(--bad)';
function f2Texto(t){                                // texto do PDF → HTML (títulos em negrito, tabelas com TAB)
  let h = '', tab = [];
  const fecha = () => { if (tab.length){ h += '<table class="f2-tab">' + tab.map(r => '<tr>' + r.map(c => `<td>${esc(c)}</td>`).join('') + '</tr>').join('') + '</table>'; tab = []; } };
  for (const l of (t || '').split('\n')){
    if (l.includes('\t')){ tab.push(l.split('\t')); continue; }
    fecha();
    h += /^[A-ZÁÉÍÓÚÂÊÔÃÕÇ][A-ZÁÉÍÓÚÂÊÔÃÕÇ \/–\-:()0-9]{6,}$/.test(l.trim()) ? `<span class="f2-h">${esc(l)}</span>` : esc(l) + '\n';
  }
  fecha(); return h;
}
const f2Fmt = n => (Math.round(n * 100) / 100).toLocaleString('pt-BR', {minimumFractionDigits:1, maximumFractionDigits:2});

async function renderFase2(){
  if (F2S.est && F2S.fase) return;                         // estação aberta: mantém
  $('#f2Lista').hidden = false; $('#f2Est').hidden = true;
  $('#f2TabEst').onclick = () => f2MostrarTab('est'); $('#f2TabDocs').onclick = () => f2MostrarTab('docs');
  if (!F2L){
    $('#f2Grid').innerHTML = '<p class="muted">Carregando estações…</p>';
    try { F2L = await api('/api/estacoes'); }
    catch (e){ $('#f2Grid').innerHTML = `<p class="muted">Não foi possível carregar as estações: ${esc(e.message)}</p>`; return; }
    opcoes($('#f2Area'), F2_AREAS, 'Todas as áreas');
    const eds = []; F2L.forEach(e => { if (!eds.some(x => x[0] === e.edicao)) eds.push([e.edicao, e.edicao_rotulo]); });
    $('#f2Ed').innerHTML = '<option value="">Todas as edições</option>' + eds.reverse().map(([v, r]) => `<option value="${esc(v)}">${esc(r)}</option>`).join('');
    ['#f2Area', '#f2Ed', '#f2Sit'].forEach(s => $(s).onchange = f2Lista);
    $('#f2Busca').oninput = f2Lista;
    $('#f2Aleat').onclick = () => { const l = f2Filtradas(); const pool = l.filter(e => f2Melhor(e.id) === null); const a = (pool.length ? pool : l); if (a.length) abrirEstacao(a[Math.floor(Math.random() * a.length)].id); };
  }
  f2Kpis(); f2Lista();
}
function f2Filtradas(){
  const ar = $('#f2Area').value, ed = $('#f2Ed').value, sit = $('#f2Sit').value;
  const b = $('#f2Busca').value.trim().toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
  return F2L.filter(e => (!ar || e.area === ar) && (!ed || e.edicao === ed)
    && (!sit || (sit === 'nao' ? f2Melhor(e.id) === null : sit === 'sim' ? f2Melhor(e.id) !== null : (f2Melhor(e.id) ?? 99) < 7))
    && (!b || (e.titulo + ' ' + e.area).toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').includes(b)));
}
function f2Kpis(){
  const feitas = F2L.filter(e => f2Melhor(e.id) !== null);
  const media = feitas.length ? feitas.reduce((a, e) => a + f2Ultima(e.id), 0) / feitas.length : null;
  const porArea = F2_AREAS.map(a => { const l = feitas.filter(e => e.area === a); return [a, l.length ? l.reduce((s, e) => s + f2Ultima(e.id), 0) / l.length : null]; }).filter(x => x[1] !== null).sort((x, y) => x[1] - y[1]);
  $('#f2Kpis').innerHTML = `
    <div class="kpi"><b>${feitas.length}<span class="small muted">/${F2L.length}</span></b><span class="small muted">estações treinadas</span></div>
    <div class="kpi"><b style="color:${media === null ? 'inherit' : f2Cor(media)}">${media === null ? '–' : f2Fmt(media)}</b><span class="small muted">nota média (última tentativa)</span></div>
    <div class="kpi"><b style="font-size:1.05rem">${porArea.length ? esc(porArea[0][0]) : '–'}</b><span class="small muted">área para reforçar${porArea.length ? ' · ' + f2Fmt(porArea[0][1]) : ''}</span></div>
    <div class="kpi"><b>${media === null ? '–' : f2Fmt(media * 10)}</b><span class="small muted">projeção em 100 pontos</span></div>`;
}
function f2Lista(){
  const l = f2Filtradas();
  $('#f2Cont').textContent = `${l.length} estação(ões)`;
  $('#f2Grid').innerHTML = l.map(e => { const m = f2Melhor(e.id), n = (F2[e.id] || []).length;
    return `<button class="f2-card" data-id="${esc(e.id)}">
      <div class="top"><span class="pill pri">${ic(F2_ICON[e.area] || 'osce')}${esc(e.area)}</span><span class="pill">${esc(e.edicao_rotulo)} · Estação ${e.estacao}</span>
      ${m !== null ? `<span class="f2-nota" style="color:${f2Cor(m)}">${f2Fmt(m)}</span>` : ''}</div>
      <b>${esc(e.titulo)}</b>
      <span class="tiny muted">${e.itens} itens no checklist${n ? ` · treinada ${n}×` : ''}${e.tambem_em.length ? ' · também: ' + esc(e.tambem_em.join(', ')) : ''}${e.pep_preliminar ? ' · PEP preliminar' : ''}</span>
    </button>`; }).join('') || '<p class="muted">Nenhuma estação com esses filtros.</p>';
  $$('#f2Grid .f2-card').forEach(b => b.onclick = () => abrirEstacao(b.dataset.id));
}

async function abrirEstacao(id){
  let e; try { e = await api('/api/estacoes/' + encodeURIComponent(id)); } catch (x){ toast('Erro: ' + x.message); return; }
  clearInterval(F2S.h);
  Object.assign(F2S, {est:e, fase:'leitura', restante:600, t0:0, marc:{}});
  $('#f2Lista').hidden = true; $('#f2Est').hidden = false;
  f2Render(); window.scrollTo({top:0, behavior:'smooth'});
}
function f2Sair(){ clearInterval(F2S.h); F2S.est = null; F2S.fase = ''; renderFase2(); window.scrollTo({top:0}); }
function f2Tempo(){ const r = Math.max(0, F2S.restante); return `${String(Math.floor(r / 60)).padStart(2, '0')}:${String(r % 60).padStart(2, '0')}`; }
function f2Iniciar(){
  F2S.fase = 'estacao'; F2S.t0 = Date.now(); F2S.restante = 600; f2Render();
  F2S.h = setInterval(() => {
    F2S.restante = 600 - Math.floor((Date.now() - F2S.t0) / 1000);
    const t = $('#f2Timer'); if (t){ t.textContent = f2Tempo(); t.classList.toggle('alerta', F2S.restante <= 120); }
    if (F2S.restante === 120) toast('⏱️ Faltam 2 minutos – feche diagnóstico e conduta!');
    if (F2S.restante <= 0){ clearInterval(F2S.h); try { navigator.vibrate && navigator.vibrate([300, 150, 300]); } catch {}
      toast('⏰ Tempo esgotado! Hora de se autoavaliar.'); f2Avaliar(); }
  }, 500);
}
function f2Avaliar(){ clearInterval(F2S.h); F2S.seg = F2S.t0 ? Math.min(600, Math.round((Date.now() - F2S.t0) / 1000)) : 0; F2S.fase = 'avaliacao'; f2Render(); window.scrollTo({top:0, behavior:'smooth'}); }

function f2Impressos(e, revelar){
  if (!e.impressos.length) return '<p class="small muted">Esta estação não tem impressos.</p>';
  return e.impressos.map((im, i) => `<details class="f2-imp"${revelar ? ' open' : ''}><summary>${ic(im.imagem ? 'img' : 'doc')}${esc(im.titulo)}<span class="tiny muted" style="margin-left:auto">${revelar ? '' : 'solicitar'}</span></summary>
    <div class="corpo">${im.texto ? `<div class="f2-txt small">${f2Texto(im.texto)}</div>` : ''}
    ${im.imagem ? `<p class="small" style="margin:${im.texto ? '10px' : '0'} 0 0">${ic('img')} Este impresso tem imagem (foto, ECG, exame de imagem ou tabela). <a href="${esc(safeHref(e.fonte.prova_pdf))}#page=${im.pagina}" target="_blank" rel="noopener">Abrir no PDF oficial do INEP – pág. ${im.pagina}</a></p>` : `<p class="tiny muted" style="margin:8px 0 0"><a href="${esc(safeHref(e.fonte.prova_pdf))}#page=${im.pagina}" target="_blank" rel="noopener">Ver o impresso original (PDF do INEP, pág. ${im.pagina})</a></p>`}</div></details>`).join('');
}
function f2Cab(e){
  return `<div class="row" style="justify-content:space-between;align-items:flex-start">
    <div><div class="row" style="gap:6px"><span class="pill pri">${ic(F2_ICON[e.area] || 'osce')}${esc(e.area)}</span><span class="pill">${esc(e.edicao_rotulo)} · Estação ${e.estacao}</span>${e.pep_preliminar ? '<span class="pill">PEP preliminar</span>' : ''}</div>
    <h2 style="margin:8px 0 0">${esc(e.titulo)}</h2></div>
    <button class="btn sec sm" id="f2Voltar">${ic('back')}Estações</button></div>`;
}
function f2Render(){
  const e = F2S.est, box = $('#f2Est');
  if (F2S.fase === 'leitura'){
    box.innerHTML = `<div class="card">${f2Cab(e)}</div>
      <div class="card"><h3>${ic('doc')}Instruções ao participante</h3><div class="f2-txt">${f2Texto(e.instrucoes) || '<span class="muted">Instruções não disponíveis em texto – veja o PDF oficial.</span>'}</div>
        <p class="tiny muted" style="margin-top:10px">Na prova você tem cerca de 1 minuto para ler as instruções antes de entrar na sala.</p></div>
      <div class="card"><h3>${ic('play')}Como vai treinar?</h3>
        <div class="seg" style="margin-top:6px">
          <button id="f2Solo"><b>${ic('user')}Sozinho(a)</b><span class="small muted">Fale em voz alta. Os impressos ficam disponíveis para você "solicitar" durante a estação.</span></button>
          <button id="f2Dupla"><b>${ic('steth')}Com um colega</b><span class="small muted">O colega faz o paciente e o avaliador: ele vê o roteiro do ator e entrega os impressos quando você pedir.</span></button>
        </div></div>`;
    $('#f2Solo').onclick = () => { F2S.dupla = false; f2Iniciar(); };
    $('#f2Dupla').onclick = () => { F2S.dupla = true; f2Iniciar(); };
  }
  if (F2S.fase === 'estacao'){
    box.innerHTML = `<div class="card f2-bar"><div><span class="tiny muted">${esc(e.area)} · ${esc(e.edicao_rotulo)}</span><div class="f2-timer" id="f2Timer">${f2Tempo()}</div></div>
        <div class="row"><button class="btn" id="f2Fim">${ic('check')}Encerrar e autoavaliar</button><button class="btn sec sm" id="f2Voltar">${ic('x')}Sair</button></div></div>
      <div class="card"><h3>${ic('doc')}Instruções ao participante</h3><div class="f2-txt">${f2Texto(e.instrucoes)}</div></div>
      <div class="card"><h3>${ic('img')}Impressos</h3><p class="small muted">${F2S.dupla ? 'O colega entrega cada impresso quando você solicitar corretamente.' : 'Abra um impresso só quando você o "solicitar" em voz alta (ex.: "solicito o exame físico").'}</p>${f2Impressos(e, false)}</div>
      ${F2S.dupla ? `<div class="card"><h3>${ic('user')}Para o colega: roteiro do paciente simulado</h3><details><summary class="small" style="cursor:pointer;font-weight:700">Mostrar roteiro (não olhe se você é o candidato!)</summary><div class="f2-roteiro f2-txt small" style="margin-top:8px">${esc(e.sintese) || 'A síntese desta edição não foi publicada – use o checklist como roteiro.'}</div></details></div>` : ''}`;
    $('#f2Fim').onclick = f2Avaliar;
  }
  if (F2S.fase === 'avaliacao') f2RenderAval();
  if (F2S.fase === 'resultado') f2RenderRes();
  const v = $('#f2Voltar'); if (v) v.onclick = () => { if (F2S.fase === 'estacao' && !confirm('Sair da estação sem avaliar?')) return; f2Sair(); };
}
function f2Opcoes(it){
  // notas: [0, parcial…, máx] → Inadequado / Parcialmente adequado / Adequado
  const n = it.notas.length ? it.notas : [0, it.max];
  if (n.length <= 3) return n.map((v, i) => ({v, cls:i === 0 ? 'v0' : i === n.length - 1 ? 'v2' : 'v1', rot:i === 0 ? 'Inadequado' : i === n.length - 1 ? 'Adequado' : 'Parcial'}));
  return n.map((v, i) => ({v, cls:i === 0 ? 'v0' : i === n.length - 1 ? 'v2' : 'v1', rot:f2Fmt(v)}));
}
function f2Nota(){ return F2S.est.checklist.reduce((a, it) => a + (it.anulado ? it.max : (F2S.marc[it.n] ?? 0)), 0); }
function f2RenderAval(){
  const e = F2S.est; let sec = '';
  const itens = e.checklist.map(it => {
    const h = it.secao && it.secao !== sec ? `<div class="f2-sec">${esc(sec = it.secao)}</div>` : '';
    const ops = f2Opcoes(it), m = F2S.marc[it.n];
    const idx = it.anulado ? 2 : m === undefined ? -1 : ops.findIndex(o => o.v === m);
    return h + `<div class="f2-item ${idx < 0 ? '' : 'm' + (ops[idx] ? ops[idx].cls.slice(1) : 2)}" data-n="${it.n}">
      <div class="row" style="justify-content:space-between;align-items:flex-start;gap:8px"><div class="f2-txt small" style="flex:1"><b>${it.n}.</b> ${esc(it.item)}</div><span class="pill">${f2Fmt(it.max)} pt</span></div>
      <div class="crit">${it.adequado ? `<span><b>Adequado:</b> ${esc(it.adequado)}</span>` : ''}${it.parcial ? `<span><b>Parcialmente adequado:</b> ${esc(it.parcial)}</span>` : ''}${it.inadequado ? `<span><b>Inadequado:</b> ${esc(it.inadequado)}</span>` : ''}</div>
      ${it.anulado ? '<span class="pill ok">Item anulado pelo INEP – pontuação atribuída a todos</span>' :
        `<div class="f2-opts">${ops.map(o => `<button class="${o.cls}${m === o.v ? ' on' : ''}" data-v="${o.v}">${o.rot} · ${f2Fmt(o.v)}</button>`).join('')}</div>`}
    </div>`; }).join('');
  const faltam = e.checklist.filter(it => !it.anulado && F2S.marc[it.n] === undefined).length;
  $('#f2Est').innerHTML = `<div class="card">${f2Cab(e)}</div>
    <div class="card f2-bar"><div><span class="tiny muted">Nota parcial</span><div class="f2-timer" id="f2Parc">${f2Fmt(f2Nota())}<span class="small muted"> / 10</span></div></div>
      <div class="row"><button class="btn sec sm" id="f2Tudo0">Marcar pendentes como inadequado</button><button class="btn" id="f2Salvar" ${faltam ? 'disabled' : ''}>${ic('check')}Ver resultado${faltam ? ` (faltam ${faltam})` : ''}</button></div></div>
    <div class="card"><h3>${ic('check')}Checklist oficial (PEP)</h3><p class="small muted">Marque com honestidade o que você <b>fez e verbalizou</b> nos 10 minutos – é assim que o avaliador pontua. Em dupla, o colega marca.</p>${itens}</div>`;
  $$('#f2Est .f2-item .f2-opts button').forEach(b => b.onclick = () => { F2S.marc[+b.closest('.f2-item').dataset.n] = +b.dataset.v; const y = window.scrollY; f2RenderAval(); window.scrollTo({top:y}); });
  $('#f2Tudo0').onclick = () => { e.checklist.forEach(it => { if (!it.anulado && F2S.marc[it.n] === undefined) F2S.marc[it.n] = 0; }); const y = window.scrollY; f2RenderAval(); window.scrollTo({top:y}); };
  $('#f2Salvar').onclick = () => {
    const nota = Math.round(f2Nota() * 100) / 100;
    (F2[e.id] = F2[e.id] || []).push({data:hoje(), nota, seg:F2S.seg || 0}); ls.set('cti_2fase', F2);
    F2S.nota = nota; F2S.fase = 'resultado'; f2Render(); window.scrollTo({top:0, behavior:'smooth'});
  };
  $('#f2Voltar').onclick = () => { if (confirm('Sair sem salvar a avaliação?')) f2Sair(); };
}
function f2RenderRes(){
  const e = F2S.est, nota = F2S.nota, hist = F2[e.id] || [];
  const perdidos = e.checklist.filter(it => !it.anulado && (F2S.marc[it.n] ?? 0) < it.max).sort((a, b) => (b.max - (F2S.marc[b.n] ?? 0)) - (a.max - (F2S.marc[a.n] ?? 0)));
  $('#f2Est').innerHTML = `<div class="card">${f2Cab(e)}</div>
    <div class="card" style="text-align:center"><span class="tiny muted">Sua nota nesta estação</span>
      <div style="font-size:3rem;font-weight:800;color:${f2Cor(nota)};line-height:1.1">${f2Fmt(nota)}<span class="small muted"> / 10</span></div>
      <p class="small muted">${nota >= 7 ? 'Ótimo desempenho! ' : nota >= 5 ? 'Bom caminho – veja onde perdeu pontos. ' : 'Refaça esta estação depois de revisar o tema. '}${F2S.seg ? `Tempo usado: ${Math.floor(F2S.seg / 60)}min ${F2S.seg % 60}s.` : ''}${hist.length > 1 ? ` Tentativas: ${hist.map(h => f2Fmt(h.nota)).join(' → ')}` : ''}</p>
      <div class="row" style="justify-content:center"><button class="btn" id="f2Refazer">${ic('play')}Refazer</button><button class="btn sec" id="f2Outra">${ic('arrow')}Outra estação</button></div></div>
    ${perdidos.length ? `<div class="card"><h3>${ic('alert')}Onde você perdeu pontos</h3>${perdidos.map(it => `<div class="f2-item m0"><div class="row" style="justify-content:space-between"><div class="small f2-txt" style="flex:1"><b>${it.n}.</b> ${esc(it.item)}</div><span class="pill">−${f2Fmt(it.max - (F2S.marc[it.n] ?? 0))}</span></div>${it.adequado ? `<div class="crit"><span><b>Para pontuar tudo:</b> ${esc(it.adequado)}</span></div>` : ''}</div>`).join('')}</div>` : ''}
    ${e.sintese ? `<div class="card"><h3>${ic('book')}Síntese oficial da estação (roteiro do paciente e do avaliador)</h3><div class="f2-txt small">${f2Texto(e.sintese)}</div></div>` : ''}
    <div class="card"><h3>${ic('img')}Impressos da estação</h3>${f2Impressos(e, true)}</div>
    ${e.referencias ? `<div class="card"><h3>${ic('book')}Referências citadas pelo INEP</h3><div class="f2-txt small">${esc(e.referencias)}</div></div>` : ''}
    <div class="card"><h3>${ic('doc')}Treino escrito complementar</h3><p class="small muted">Estes documentos são sugeridos para ampliar a preparação. Só fazem parte da pontuação histórica quando o PEP original os exige explicitamente.</p><div class="row">${f2DocsRecomendados(e).map(id=>{const d=D2_DOCS.find(x=>x.id===id);return `<button class="btn sec sm f2-doc-go" data-doc="${id}">${ic('doc')}${esc(d.titulo)}</button>`}).join('')}</div></div>
    <div class="card"><p class="small muted" style="margin:0">Fonte: INEP – Revalida ${esc(e.edicao_rotulo)}, 2ª etapa. <a href="${esc(safeHref(e.fonte.prova_pdf))}#page=${e.fonte.pagina_prova}" target="_blank" rel="noopener">Caderno da prova</a> · <a href="${esc(safeHref(e.fonte.pep_pdf))}#page=${e.fonte.pagina_pep}" target="_blank" rel="noopener">PEP oficial${e.pep_preliminar ? ' (preliminar)' : ''}</a></p></div>`;
  $('#f2Refazer').onclick = () => abrirEstacao(e.id);
  $('#f2Outra').onclick = f2Sair;
  $('#f2Voltar').onclick = f2Sair;
  $$('#f2Est .f2-doc-go').forEach(b => b.onclick = () => d2IrDeEstacao(b.dataset.doc));
}

/* ============================================================ 2ª FASE – documentos escritos */
let D2H = ls.get('cti_doc_treinos', []);
const D2 = {id:null, modo:'prova', inicio:0};
const D2_DOCS = [
  {id:'soap', cat:'registro', titulo:'SOAP', badge:'Modelo técnico CTI', nivel:'cti', desc:'Registro estruturado em Subjetivo, Objetivo, Avaliação e Plano. Útil em APS, ambulatório e acompanhamento longitudinal.', fonte:'Estrutura SOAP – modelo técnico de treinamento; o INEP pode cobrar o conteúdo sem impor um formulário nacional único.', url:'', caso:'Mulher, 58 anos, DM2 há 10 anos e hipertensão. Retorna à APS por glicemias elevadas. Refere poliúria e polidipsia leves, sem perda ponderal, febre ou vômitos. Usa metformina 850 mg 2x/dia irregularmente. PA 146/88 mmHg, FC 82 bpm, IMC 31 kg/m². Exame físico sem sinais de desidratação. HbA1c 9,2%, creatinina 0,9 mg/dL, eTFG preservada. Não trouxe registro de glicemias. Último exame de fundo de olho há 2 anos.', fields:[
    ['s','S – Subjetivo','Sintomas, história, adesão, percepção do paciente e negativos relevantes.',2.5,80],
    ['o','O – Objetivo','Sinais vitais, exame físico e resultados relevantes.',2.0,60],
    ['a','A – Avaliação','Problemas ativos, hipótese/diagnóstico, controle e riscos.',2.5,55],
    ['p','P – Plano','Condutas, exames, tratamento, educação, seguimento e sinais de alarme.',3.0,90]],
   model:{s:'Retorno por controle inadequado do DM2. Refere poliúria/polidipsia leves. Nega febre, vômitos, perda ponderal e sintomas de descompensação aguda. Adesão irregular à metformina 850 mg 2x/dia. Sem registro domiciliar de glicemias. Fundo de olho há 2 anos.',o:'PA 146/88 mmHg, FC 82 bpm, IMC 31 kg/m². Sem sinais clínicos de desidratação. HbA1c 9,2%; creatinina 0,9 mg/dL, função renal preservada.',a:'1) DM2 com controle glicêmico inadequado, provavelmente associado à baixa adesão; sem sinais de emergência hiperglicêmica. 2) HAS acima da meta na consulta. 3) Obesidade. 4) Rastreamento de complicações crônicas incompleto.',p:'Reforçar adesão e técnica de uso das medicações; revisar alimentação/atividade física. Reavaliar esquema antidiabético conforme protocolo e perfil clínico. Solicitar/atualizar rastreio de complicações: albuminúria, exame oftalmológico e avaliação dos pés; revisar perfil lipídico e risco CV. Orientar automonitorização quando indicada. Programar retorno com resultados e metas. Orientar procura imediata se vômitos persistentes, alteração do sensório, dispneia ou sinais de desidratação.'}},
  {id:'evolucao', cat:'registro', titulo:'Evolução de prontuário / continuidade assistencial', badge:'Modelo técnico CTI', nivel:'cti', desc:'Evolução que permite ao próximo profissional saber onde o caso parou, o que mudou, o que foi feito e quais são as pendências.', fonte:'Modelo técnico de treinamento CTI focado em continuidade do cuidado e comunicação segura entre profissionais.', url:'', caso:'Homem, 72 anos, internado há 48 h por pneumonia adquirida na comunidade. Em ceftriaxona + azitromicina. Ontem necessitava O₂ 3 L/min; hoje está em O₂ 2 L/min com SpO₂ 94%. Afebril há 24 h, FR 20 irpm, PA 124/76, FC 88. Mantém tosse produtiva, alimentando-se melhor. Ausculta: crepitações em base direita, sem esforço respiratório. Leucócitos caíram de 17.000 para 12.400/mm³; PCR em queda. Hemoculturas sem crescimento até o momento. Creatinina 1,0 mg/dL. Fisioterapia respiratória realizada. Pendências: reavaliar necessidade de O₂, resultado final das culturas e possibilidade de transição para via oral/alta nas próximas 24–48 h.', fields:[
    ['contexto','Data/turno e contexto','Identifique o momento da evolução e motivo da internação/seguimento.',1.0,30],
    ['estado','Estado atual e mudança desde a última avaliação','Diga se melhorou, piorou ou permaneceu estável e em quê.',1.2,60],
    ['objetivo','Dados objetivos relevantes','Sinais vitais, exame físico e suporte atual.',1.2,60],
    ['resultados','Resultados e tendências','Exames, culturas, imagem ou tendências que mudam conduta.',1.0,45],
    ['problemas','Problemas ativos / avaliação','Liste os problemas em ordem de prioridade.',1.3,55],
    ['feito','Condutas já realizadas','O que foi feito desde a última avaliação.',1.0,45],
    ['resposta','Resposta às condutas','Como o paciente respondeu ao tratamento.',0.8,40],
    ['pendencias','Pendências','O que ainda falta resolver ou checar.',1.0,45],
    ['proximos','Próximos passos','Plano concreto para o próximo turno/profissional.',1.0,55],
    ['alertas','Alertas / critérios de reavaliação','O que exigiria mudança imediata de conduta.',0.5,35]],
   model:{contexto:'27/09/2026 – evolução diurna. D2 de internação por pneumonia adquirida na comunidade.',estado:'Evolui com melhora clínica progressiva: afebril há 24 h, menor necessidade de oxigênio e melhor aceitação alimentar. Mantém tosse produtiva, sem piora respiratória.',objetivo:'PA 124/76 mmHg, FC 88 bpm, FR 20 irpm, SpO₂ 94% em O₂ 2 L/min. Sem esforço respiratório. Crepitações em base direita.',resultados:'Leucócitos 17.000 → 12.400/mm³; PCR em queda. Creatinina 1,0 mg/dL. Hemoculturas sem crescimento até o momento; resultado final pendente.',problemas:'1) Pneumonia adquirida na comunidade em melhora. 2) Hipoxemia leve em desmame de O₂. 3) Necessidade de definir transição de antibiótico e critérios de alta.',feito:'Mantidos ceftriaxona + azitromicina, oxigenoterapia e fisioterapia respiratória. Iniciado desmame de O₂ conforme tolerância.',resposta:'Boa resposta clínica e laboratorial, com redução da necessidade de O₂ e marcadores inflamatórios em queda.',pendencias:'Reavaliar saturação em ar ambiente/desmame de O₂; acompanhar resultado final das culturas; confirmar estabilidade clínica e tolerância oral.',proximos:'Se mantiver estabilidade e SpO₂ adequada sem suporte, considerar transição para antibiótico oral e planejamento de alta em 24–48 h. Manter fisioterapia, hidratação e mobilização.',alertas:'Reavaliar imediatamente se febre recorrente, aumento da necessidade de O₂, hipotensão, taquipneia, alteração do sensório ou piora clínica/laboratorial.'}},
  {id:'encaminhamento', cat:'comunicacao', titulo:'Encaminhamento / referência', badge:'Modelo técnico CTI', nivel:'cti', desc:'Treino de referência objetiva: motivo, gravidade, dados relevantes, o que já foi feito e o que se espera do serviço de destino.', fonte:'Modelo técnico CTI baseado em comunicação clínica segura; conferir fluxos locais do SUS quando aplicáveis.', url:'', caso:'Homem, 54 anos, dor retroesternal em aperto aos esforços há 3 semanas, duração 5–10 min, melhora ao repouso. Hoje teve episódio ao caminhar duas quadras. HAS e tabagismo. Assintomático na consulta. PA 138/84, FC 76. ECG em repouso sem supra de ST. Sem sinais de insuficiência cardíaca.', fields:[['identificacao','Identificação e contexto','Paciente e unidade/serviço de origem.',1,30],['motivo','Motivo objetivo do encaminhamento','Por que está encaminhando e qual a prioridade.',2,55],['resumo','Resumo clínico relevante','História, fatores de risco e achados.',2,70],['exames','Exames/resultados disponíveis','Inclua resultados relevantes já disponíveis.',1,35],['condutas','Condutas já realizadas','O que já foi feito antes do encaminhamento.',1,35],['destino','Destino e pergunta clínica','Especialidade/serviço e o que precisa ser avaliado.',1.5,40],['alertas','Risco/alertas e orientação','Sinais de alarme e orientação ao paciente.',1.5,45]], model:{identificacao:'Paciente masculino, 54 anos, avaliado na APS.',motivo:'Encaminhamento prioritário para avaliação cardiológica por dor torácica típica aos esforços, sugestiva de angina estável de início recente.',resumo:'Dor retroesternal em aperto, desencadeada por esforço, dura 5–10 min e melhora ao repouso; início há 3 semanas. HAS e tabagismo. Atualmente assintomático. PA 138/84 mmHg, FC 76 bpm, sem sinais de congestão.',exames:'ECG de repouso sem supradesnivelamento de ST.',condutas:'Orientado evitar esforços intensos até avaliação e procurar emergência se dor em repouso, prolongada ou associada a dispneia/sudorese/síncope.',destino:'Cardiologia – estratificação de doença arterial coronariana e definição de investigação/terapia.',alertas:'Orientado atendimento imediato se mudança do padrão da dor ou sintomas de alarme.'}},
  {id:'receita', cat:'prescricao', titulo:'Receita simples', badge:'Modelo técnico CTI', nivel:'cti', desc:'Treine medicamento, apresentação, dose, via, frequência, duração, quantidade e orientações.', fonte:'Modelo técnico CTI; a prescrição real deve obedecer legislação, protocolos e identificação profissional vigentes.', url:'', caso:'Adulto com diagnóstico confirmado de faringoamigdalite estreptocócica, sem alergia a penicilinas, função renal normal e sem sinais de complicação.', fields:[['medicamento','Medicamento e apresentação','Nome, concentração e forma farmacêutica.',2,25],['posologia','Dose, via e frequência','Escreva sem abreviações ambíguas.',3,35],['duracao','Duração e quantidade','Tempo total e quantidade necessária.',2,25],['orientacoes','Orientações ao paciente','Como usar, adesão e sinais de alarme.',2,50],['identificacao','Data e identificação do prescritor','No treino, descreva os elementos necessários.',1,20]], model:{medicamento:'Amoxicilina 500 mg – cápsulas.',posologia:'Tomar 1 cápsula por via oral a cada 8 horas.',duracao:'Usar por 10 dias. Dispensar 30 cápsulas.',orientacoes:'Completar o tratamento mesmo com melhora clínica; retornar se piora, dificuldade respiratória/deglutição, rash importante ou sinais de reação alérgica.',identificacao:'Data; nome do médico; CRM/UF; assinatura/identificação conforme regra aplicável.'}},
  {id:'exames', cat:'comunicacao', titulo:'Solicitação de exames', badge:'Modelo técnico CTI', nivel:'cti', desc:'Solicitação orientada por hipótese e pergunta clínica, evitando pedidos genéricos.', fonte:'Modelo técnico CTI.', url:'', caso:'Mulher, 32 anos, fadiga, menorragia e palidez conjuntival. Sem sinais de instabilidade. Suspeita de anemia ferropriva.', fields:[['hipotese','Hipótese/pergunta clínica','Explique o motivo da investigação.',2,35],['exames','Exames solicitados','Liste os exames pertinentes.',4,35],['prioridade','Prioridade/urgência','Defina se eletivo ou urgente e por quê.',1.5,30],['contexto','Contexto clínico relevante','Dados que ajudam a interpretar os exames.',1.5,40],['identificacao','Identificação/data','Elementos formais mínimos.',1,20]], model:{hipotese:'Investigação de anemia, provável deficiência de ferro, em paciente com menorragia.',exames:'Hemograma completo; ferritina; ferro sérico e saturação de transferrina conforme disponibilidade/protocolo. Considerar reticulócitos e investigação etiológica do sangramento conforme avaliação clínica.',prioridade:'Eletivo prioritário; paciente hemodinamicamente estável e sem sinais de sangramento agudo importante.',contexto:'Fadiga, menorragia e palidez conjuntival; sem instabilidade hemodinâmica.',identificacao:'Identificação da paciente, data, solicitante e registro profissional.'}},
  {id:'atestado', cat:'comunicacao', titulo:'Atestado médico', badge:'Modelo técnico CTI', nivel:'cti', desc:'Treine texto objetivo, período de afastamento e informações estritamente necessárias.', fonte:'Modelo técnico CTI; observar normas éticas e legais vigentes.', url:'', caso:'Paciente com gastroenterite aguda sem sinais de gravidade, avaliado hoje, necessitando repouso e hidratação domiciliar por 2 dias.', fields:[['texto','Texto do atestado','Declare atendimento e necessidade de afastamento sem expor dados desnecessários.',5,70],['periodo','Período','Informe duração e data inicial.',2,20],['identificacao','Identificação/data do profissional','Elementos formais.',2,25],['privacidade','Privacidade','Explique como trataria diagnóstico/CID no documento.',1,25]], model:{texto:'Atesto, para os devidos fins, que o(a) paciente foi avaliado(a) nesta data e necessita afastar-se de suas atividades habituais por motivo de saúde.',periodo:'Afastamento por 2 (dois) dias, a contar de hoje.',identificacao:'Local/data; nome do médico; CRM/UF; assinatura/identificação.',privacidade:'Diagnóstico/CID somente quando pertinente e com observância do consentimento e das normas aplicáveis.'}},
  {id:'relatorio', cat:'comunicacao', titulo:'Relatório médico', badge:'Modelo técnico CTI', nivel:'cti', desc:'Síntese clínica com diagnóstico, evolução, exames, tratamento e finalidade do relatório.', fonte:'Modelo técnico CTI.', url:'', caso:'Paciente de 67 anos com AVC isquêmico há 3 meses, hemiparesia direita residual, em fisioterapia e terapia ocupacional, necessita relatório para continuidade da reabilitação.', fields:[['finalidade','Finalidade','Diga para que o relatório está sendo emitido.',1,30],['historia','História e diagnóstico','Resumo objetivo do quadro e diagnóstico.',2,60],['estado','Estado funcional atual','Déficits, limitações e evolução.',2,50],['tratamento','Tratamentos e reabilitação','O que está sendo realizado.',1.5,45],['recomendacao','Recomendação/necessidade atual','O que deve continuar/ser providenciado.',2,45],['formal','Data e identificação','Elementos formais.',1.5,20]], model:{finalidade:'Relatório para continuidade de programa de reabilitação multiprofissional.',historia:'Paciente, 67 anos, com antecedente de AVC isquêmico há 3 meses, evoluindo com déficit motor residual à direita.',estado:'Mantém hemiparesia direita, com limitação funcional para atividades que exigem marcha e destreza do membro superior direito; apresenta evolução parcial com reabilitação.',tratamento:'Em acompanhamento clínico, fisioterapia e terapia ocupacional.',recomendacao:'Recomenda-se continuidade da reabilitação multiprofissional, com reavaliação funcional periódica e prevenção secundária do AVC.',formal:'Local/data, identificação do profissional e CRM/UF.'}},
  {id:'obito', cat:'oficial', titulo:'Declaração de Óbito – cadeia causal', badge:'Base oficial MS/SIM', nivel:'oficial', desc:'Treino da Parte I e II da DO, com foco em causa imediata, causas antecedentes e causa básica.', fonte:'Base oficial: Ministério da Saúde / Sistema de Informações sobre Mortalidade (SIM). Layout do CTI é simplificado para treino.', url:'https://www.gov.br/saude/pt-br/composicao/svsa/sistemas-de-informacao/sim', caso:'Homem, 68 anos, com aterosclerose coronariana conhecida, internado após infarto agudo do miocárdio com supradesnivelamento de ST. Evoluiu com choque cardiogênico refratário e faleceu 18 horas após a admissão. Tinha diabetes mellitus tipo 2 e hipertensão arterial sistêmica há muitos anos.', fields:[['a','Parte I – linha a (causa imediata)','Doença/condição que levou diretamente ao óbito.',2,20],['b','Parte I – linha b (devido a)','Condição antecedente da causa imediata.',2,20],['c','Parte I – linha c (causa básica, se aplicável)','Evento/doença que iniciou a cadeia.',2,20],['intervalos','Intervalos aproximados','Tempo entre início de cada condição e óbito.',1,20],['parte2','Parte II – outras condições contribuintes','Condições relevantes que contribuíram, sem fazer parte da cadeia principal.',2,30],['evitar','O que não usar como causa isolada','Explique por que mecanismo terminal isolado é inadequado.',1,30]], model:{a:'Choque cardiogênico – horas.',b:'Infarto agudo do miocárdio com supradesnivelamento do segmento ST – cerca de 18 horas.',c:'Doença aterosclerótica das artérias coronárias – anos.',intervalos:'Registrar intervalos aproximados coerentes com a história clínica.',parte2:'Diabetes mellitus tipo 2; hipertensão arterial sistêmica.',evitar:'Evitar registrar apenas “parada cardiorrespiratória”, pois é mecanismo terminal e não descreve a doença que iniciou a cadeia causal.'}},
  {id:'sinan', cat:'oficial', titulo:'Notificação compulsória / SINAN', badge:'Base oficial MS/SINAN', nivel:'oficial', desc:'Treino dos campos essenciais de uma notificação e investigação epidemiológica.', fonte:'Base oficial: Ministério da Saúde / SINAN. O CTI usa formulário simplificado para treino; conferir a ficha específica vigente.', url:'https://www.gov.br/saude/pt-br/composicao/svsa/sistemas-de-informacao/sinan', caso:'Mulher, 27 anos, residente em Recife, iniciou há 3 dias febre alta, cefaleia, dor retro-orbitária, mialgia e náuseas. Sem sinais de alarme. Prova do laço não realizada. Suspeita clínica de dengue em atendimento na UBS. Data do início dos sintomas conhecida; sem viagem internacional recente.', fields:[['agravo','Agravo/doença notificada','Identifique o evento.',1.5,15],['paciente','Identificação e residência','Dados disponíveis do paciente e local de residência.',1.5,35],['inicio','Data de início dos sintomas','Registre quando começou.',1,15],['criterios','Dados clínicos/epidemiológicos','Sintomas e elementos que sustentam a suspeita.',2,50],['classificacao','Classificação no momento da notificação','Suspeito/confirmado conforme o caso e estágio da investigação.',1.5,25],['unidade','Unidade notificadora e data','Onde/quando foi notificado.',1.5,25],['pendencias','Pendências de investigação','O que ainda precisa ser obtido/confirmado.',1,30]], model:{agravo:'Dengue – caso suspeito.',paciente:'Paciente feminina, 27 anos, residente em Recife/PE. Preencher apenas os identificadores fornecidos no caso.',inicio:'Início dos sintomas: há 3 dias (registrar a data correspondente no dia da simulação).',criterios:'Febre alta, cefaleia, dor retro-orbitária, mialgia e náuseas; sem sinais de alarme no momento.',classificacao:'Caso suspeito de dengue no momento da notificação; confirmação/classificação final dependerá da investigação e critérios vigentes.',unidade:'UBS notificadora; registrar data da notificação e identificação do serviço/profissional conforme ficha vigente.',pendencias:'Completar dados epidemiológicos e laboratoriais quando indicados, acompanhar evolução e sinais de alarme e atualizar encerramento/classificação final.'}},
  {id:'controle', cat:'oficial', titulo:'Receituário de controle especial', badge:'Referência oficial Anvisa', nivel:'oficial', desc:'Treino dos campos e requisitos formais; o tipo de receituário depende da substância e da norma vigente.', fonte:'Referência oficial: Anvisa / Sistema Nacional de Controle de Receituários. O CTI não substitui o receituário real.', url:'https://www.gov.br/anvisa/pt-br/assuntos/medicamentos/controlados/sncr/modelos-de-receituarios', caso:'Treino de forma documental: paciente adulto em seguimento regular necessita prescrição de medicamento sujeito a controle especial já definido pelo caso da estação. Foque nos campos formais e não em escolher o fármaco.', fields:[['paciente','Identificação do paciente','Dados exigidos pelo receituário aplicável.',1.5,30],['medicamento','Medicamento/apresentação/concentração','Escreva de forma completa.',2,25],['posologia','Dose, via, frequência e duração','Sem abreviações ambíguas.',2,35],['quantidade','Quantidade por extenso/quando exigido','Conforme modelo e norma aplicável.',1,20],['prescritor','Identificação do prescritor','Nome, registro, endereço/identificação conforme modelo.',1.5,30],['data','Data e assinatura','Elementos formais.',1,15],['alerta','Checagem regulatória','Explique que deve conferir o tipo de receituário e limites vigentes para a substância.',1,35]], model:{paciente:'Preencher todos os campos de identificação exigidos no modelo aplicável.',medicamento:'Registrar nome do medicamento, concentração e forma farmacêutica de maneira legível e sem ambiguidades.',posologia:'Registrar dose, via, frequência e duração do tratamento.',quantidade:'Informar quantidade total conforme as exigências do receituário aplicável, inclusive por extenso quando previsto.',prescritor:'Nome e identificação profissional, além dos demais campos exigidos no modelo vigente.',data:'Data e assinatura/identificação conforme norma vigente.',alerta:'Antes da emissão real, conferir na Anvisa a lista da substância, o tipo de receituário, validade, quantidade máxima e demais limites atuais.'}},
  {id:'contrarreferencia', cat:'comunicacao', titulo:'Referência e contrarreferência', badge:'Modelo técnico CTI', nivel:'cti', desc:'Comunicação bidirecional entre níveis de atenção, com resposta clara ao motivo do encaminhamento.', fonte:'Modelo técnico CTI; fluxos e formulários podem variar localmente.', url:'', caso:'Paciente foi encaminhado da APS ao ambulatório de endocrinologia por DM2 descompensado. Após ajuste terapêutico e exclusão de complicação aguda, retorna para seguimento compartilhado na APS.', fields:[['origem','Origem/destino e motivo inicial','Contextualize a referência.',1.5,35],['avaliacao','Avaliação realizada','O que o serviço especializado concluiu.',2,60],['conduta','Condutas e mudanças terapêuticas','O que foi feito/modificado.',2,55],['pendencias','Pendências/monitorização','O que a APS precisa acompanhar.',1.5,45],['criterios','Critérios de retorno/reencaminhamento','Quando retornar ao especialista/urgência.',1.5,45],['comunicacao','Plano compartilhado','Responsabilidades e continuidade do cuidado.',1.5,45]], model:{origem:'Contrarreferência à APS após avaliação endocrinológica por DM2 com controle inadequado.',avaliacao:'Confirmado controle glicêmico insuficiente, sem evidência de descompensação aguda; revisado risco cardiovascular e rastreamento de complicações.',conduta:'Ajustado esquema terapêutico conforme perfil clínico e reforçadas medidas de adesão/estilo de vida.',pendencias:'APS deve acompanhar glicemias/HbA1c, PA, função renal, albuminúria, avaliação dos pés e rastreamentos recomendados.',criterios:'Reencaminhar se falha persistente de controle apesar de otimização, suspeita de complicação complexa ou necessidade de terapia especializada; urgência se sinais de descompensação aguda.',comunicacao:'Seguimento longitudinal principal na APS, com metas registradas e retorno especializado conforme critérios definidos.'}},
  {id:'alta', cat:'alta', titulo:'Orientação de alta', badge:'Modelo técnico CTI', nivel:'cti', desc:'Orientações compreensíveis sobre diagnóstico, medicações, cuidados, sinais de alarme e seguimento.', fonte:'Modelo técnico CTI.', url:'', caso:'Paciente recebeu alta após internação por pneumonia, está afebril, em ar ambiente, estável e com antibiótico oral para completar tratamento.', fields:[['diagnostico','O que aconteceu','Explique o diagnóstico e situação atual em linguagem clara.',1.5,45],['medicacoes','Medicações na alta','Nome, uso e mudanças relevantes.',2,50],['cuidados','Cuidados em casa','Hidratação, atividade, alimentação e medidas específicas.',1.5,45],['retorno','Seguimento','Quando e onde retornar.',1.5,35],['alarme','Sinais de alarme','Quando procurar urgência.',2.5,55],['documentos','Documentos/resultados pendentes','O que deve levar ou acompanhar.',1,30]], model:{diagnostico:'Alta após pneumonia adquirida na comunidade, com melhora clínica, sem febre e mantendo boa oxigenação em ar ambiente.',medicacoes:'Completar o antibiótico oral conforme prescrição; revisar demais medicamentos de uso habitual e suspensões/ajustes feitos durante a internação.',cuidados:'Manter hidratação, alimentação conforme tolerância, mobilização gradual e repouso relativo; evitar tabagismo.',retorno:'Retorno na APS/serviço definido em prazo adequado para reavaliação clínica; levar documentos e resultados disponíveis.',alarme:'Procurar urgência se falta de ar, febre persistente/recorrente, confusão, dor torácica importante, cianose, incapacidade de ingerir líquidos ou piora geral.',documentos:'Acompanhar resultados ainda pendentes, se houver, e levar resumo de alta e lista atualizada de medicamentos.'}},
  {id:'prescricao_hosp', cat:'prescricao', titulo:'Prescrição hospitalar', badge:'Modelo técnico CTI', nivel:'cti', desc:'Treine dieta, hidratação, medicamentos, profilaxias, monitorização e cuidados de enfermagem.', fonte:'Modelo técnico CTI; protocolos institucionais prevalecem.', url:'', caso:'Adulto internado por pneumonia comunitária, hemodinamicamente estável, sem insuficiência renal, tolerando dieta oral, SpO₂ 92% em ar ambiente e sem contraindicação a profilaxia de TEV.', fields:[['suporte','Dieta, hidratação e suporte','Prescrições gerais e oxigênio conforme necessidade.',2,45],['tratamento','Tratamento específico','Antimicrobianos/terapia principal conforme caso.',2,45],['sintomaticos','Sintomáticos e PRN','Antitérmico/analgesia etc., com critérios.',1.5,35],['profilaxia','Profilaxias','TEV, proteção gástrica apenas quando indicada etc.',1.5,35],['monitorizacao','Monitorização','Sinais vitais, saturação, balanço e reavaliações.',1.5,45],['cuidados','Cuidados e reabilitação','Mobilização, fisioterapia, enfermagem e medidas adicionais.',1.5,45]], model:{suporte:'Dieta conforme tolerância; hidratação conforme estado clínico; oxigênio suplementar se necessário para manter meta de saturação apropriada.',tratamento:'Antibioticoterapia conforme protocolo local para pneumonia comunitária e gravidade, com ajuste por alergias, função renal e resultados microbiológicos.',sintomaticos:'Analgésico/antitérmico se dor ou febre, com dose e intervalo definidos e limites diários.',profilaxia:'Avaliar e prescrever profilaxia de tromboembolismo venoso se indicada e sem contraindicação; outras profilaxias somente conforme risco.',monitorizacao:'Sinais vitais, saturação, padrão respiratório, estado mental, diurese e reavaliação clínica/laboratorial conforme evolução.',cuidados:'Mobilização precoce e fisioterapia respiratória quando indicada; prevenção de quedas e cuidados de enfermagem pertinentes.'}},
  {id:'resumo_alta', cat:'alta', titulo:'Resumo de alta', badge:'Modelo técnico CTI', nivel:'cti', desc:'Documento de transição: motivo da internação, diagnósticos, evolução, exames, tratamentos, condição de alta e plano.', fonte:'Modelo técnico CTI.', url:'', caso:'Paciente de 72 anos internado por pneumonia comunitária, tratado por 4 dias, evoluiu com resolução da hipoxemia, permanece estável e seguirá tratamento oral e acompanhamento na APS.', fields:[['motivo','Motivo da internação e diagnósticos','Por que internou e diagnósticos principais.',1.5,45],['evolucao','Resumo da evolução','Principais eventos e resposta ao tratamento.',2,70],['exames','Exames relevantes','Resultados que influenciaram diagnóstico/conduta.',1.5,50],['tratamento','Tratamentos realizados','Terapias principais e mudanças.',1.5,50],['condicao','Condição na alta','Estado clínico e funcional ao sair.',1,35],['medicacoes','Medicações de alta','O que continuará e por quanto tempo.',1,40],['seguimento','Seguimento e pendências','Retorno, exames pendentes e responsabilidades.',1.5,55]], model:{motivo:'Internação por pneumonia adquirida na comunidade associada a hipoxemia.',evolucao:'Recebeu antibioticoterapia e oxigenoterapia, com melhora progressiva, resolução da febre e retirada do oxigênio suplementar. Sem intercorrências maiores.',exames:'Registrar imagem e exames laboratoriais relevantes, tendências e resultados microbiológicos disponíveis.',tratamento:'Antibioticoterapia, suporte de oxigênio enquanto necessário, fisioterapia/mobilização e medidas de suporte.',condicao:'Alta hemodinamicamente estável, afebril, em ar ambiente e tolerando dieta/medicações por via oral.',medicacoes:'Completar esquema oral conforme prescrição e manter demais medicamentos reconciliados.',seguimento:'Acompanhamento na APS; orientar sinais de alarme; acompanhar eventuais resultados pendentes e reavaliar conforme quadro clínico.'}}
];

function f2MostrarTab(tab){
  const docs = tab === 'docs';
  $('#f2EstacoesPane').hidden = docs; $('#f2DocsPane').hidden = !docs;
  $('#f2TabEst').classList.toggle('on', !docs); $('#f2TabDocs').classList.toggle('on', docs);
  if (docs) d2RenderHome();
}
const D2_KEYS = {
  soap:{
    s:[['poli','sintomas de hiperglicemia'],['metform','adesão/medicação'],['nega','negativos relevantes']],
    o:[['146','pressão arterial'],['9,2','hba1c'],['creatin','função renal']],
    a:[['diabetes','dm2'],['controle','descompens'],['hipertens','has'],['obes','imc']],
    p:[['ades','medicação'],['albumin','renal'],['oftal','fundo de olho'],['pé','pes'],['retorno','seguimento']]
  },
  evolucao:{
    contexto:[['pneumonia','diagnóstico/contexto'],['d2','48 h','dia 2','momento da evolução']],
    estado:[['melhora','evolução clínica'],['afeb','febre'],['oxig','o2','satur','spo2']],
    objetivo:[['124','pa'],['88','fc'],['20','fr'],['94','spo2','satur'],['crepit','ausculta']],
    resultados:[['12.400','12400','leuc'],['pcr','inflamat'],['cultura','hemocultura'],['creatin']],
    problemas:[['pneumonia'],['hipox','oxig','o2'],['alta','transição','antibiótico oral']],
    feito:[['ceftria','azitro','antibió'],['oxig','o2'],['fisioter']],
    resposta:[['melhora'],['oxig','o2'],['inflamat','leuc','pcr']],
    pendencias:[['oxig','o2','ar ambiente'],['cultura'],['alta','via oral','oral']],
    proximos:[['desmame','ar ambiente','oxig'],['oral','alta'],['reavali','monitor']],
    alertas:[['febre'],['oxig','dispne','taquip'],['hipotens'],['sensório','sensorio','confus']]
  },
  encaminhamento:{motivo:[['angina','dor torácica','dor toracica'],['prior','cardio']],resumo:[['esfor','repous'],['hipertens','has'],['tabag']],exames:[['ecg']],destino:[['cardio'],['estrat','coronar']]},
  obito:{a:[['choque cardiog']],b:[['infarto','iam']],c:[['ateroscler','coronar']],parte2:[['diabetes','dm2'],['hipertens','has']],evitar:[['parada','mecanismo']]},
  sinan:{agravo:[['dengue']],inicio:[['3 dias','início','inicio']],criterios:[['febre'],['retro-orbit','mialg']],classificacao:[['suspeit']]}
};
function d2Norm(s){return String(s||'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'');}
function d2AvaliaCampo(doc, f, v){
  const groups=D2_KEYS[doc.id]?.[f[0]]||[]; const t=d2Norm(v); const min=f[4];
  if(!v.trim()) return {frac:0, miss:groups.map(g=>g[g.length-1])};
  const lenFrac=Math.min(1,v.trim().length/Math.max(1,min));
  if(!groups.length) return {frac:lenFrac>=1?1:Math.max(.35,lenFrac*.75),miss:[]};
  let hit=0, miss=[];
  groups.forEach(g=>{const label=g[g.length-1],terms=g.slice(0,-1);const ok=terms.some(x=>t.includes(d2Norm(x)));if(ok)hit++;else miss.push(label)});
  const cov=hit/groups.length;
  return {frac:Math.min(1,.35*lenFrac+.65*cov),miss};
}

function d2Hist(id){ return D2H.filter(x => x.id === id); }
function d2Kpis(){
  const n=D2H.length, med=n?D2H.reduce((a,x)=>a+x.nota,0)/n:null;
  const soap=d2Hist('soap'), evo=d2Hist('evolucao');
  $('#d2Kpis').innerHTML=`<div class="kpi"><b>${n}</b><span class="small muted">documentos treinados</span></div><div class="kpi"><b>${med===null?'–':f2Fmt(med)}</b><span class="small muted">média / 10</span></div><div class="kpi"><b>${soap.length?f2Fmt(soap[soap.length-1].nota):'–'}</b><span class="small muted">último SOAP</span></div><div class="kpi"><b>${evo.length?f2Fmt(evo[evo.length-1].nota):'–'}</b><span class="small muted">última evolução</span></div>`;
}
function d2RenderHome(){
  $('#d2Treino').hidden=true; $('#d2Grid').hidden=false; d2Kpis();
  const filtro=$('#d2Filtro')?.value||'';
  const l=D2_DOCS.filter(d=>!filtro||d.cat===filtro);
  $('#d2Grid').innerHTML=l.map(d=>{const h=d2Hist(d.id), u=h.length?h[h.length-1].nota:null; return `<button class="doc-card" data-id="${d.id}"><div class="row" style="justify-content:space-between"><span class="doc-badge ${d.nivel}">${esc(d.badge)}</span>${u!==null?`<span class="pill">última ${f2Fmt(u)}</span>`:''}</div><h3>${esc(d.titulo)}</h3><p>${esc(d.desc)}</p><div class="row"><span class="tiny muted">${h.length?`${h.length} treino(s)`:'ainda não treinado'}</span><span class="tiny" style="margin-left:auto;color:var(--red);font-weight:800">Treinar →</span></div></button>`}).join('');
  $$('#d2Grid .doc-card').forEach(b=>b.onclick=()=>d2Abrir(b.dataset.id));
  if ($('#d2Filtro')) $('#d2Filtro').onchange=d2RenderHome;
}
function d2Abrir(id){
  const d=D2_DOCS.find(x=>x.id===id); if(!d)return; D2.id=id; D2.inicio=Date.now();
  $('#d2Grid').hidden=true; const box=$('#d2Treino'); box.hidden=false;
  const hist=d2Hist(id);
  box.innerHTML=`<div class="card"><div class="row" style="justify-content:space-between;align-items:flex-start"><div><span class="doc-badge ${d.nivel}">${esc(d.badge)}</span><h2 style="margin:8px 0 4px">${esc(d.titulo)}</h2><p class="small muted" style="margin:0">${esc(d.desc)}</p></div><button class="btn sec sm" id="d2Voltar">${ic('back')}Documentos</button></div></div>
    <div class="doc-editor"><div>
      <div class="card"><h3>${ic('steth')}Caso para treino</h3><div class="doc-case">${esc(d.caso)}</div></div>
      <div class="card"><div class="row" style="justify-content:space-between"><h3 style="margin:0">${ic('doc')}Preencha o documento</h3><span class="pill">nota de treino CTI · 0–10</span></div><div class="doc-fields" style="margin-top:12px">${d.fields.map(f=>`<div class="doc-field"><label>${esc(f[1])}</label><div class="tiny muted" style="margin-bottom:5px">${esc(f[2])}</div><textarea id="d2_${f[0]}" placeholder="Escreva como faria na prova..."></textarea></div>`).join('')}</div><div class="row" style="margin-top:14px"><button class="btn" id="d2Corrigir">${ic('check')}Corrigir e comparar</button><button class="btn sec" id="d2Limpar">Limpar</button></div></div>
    </div><aside><div class="card"><h3>${ic('book')}Base de referência</h3><p class="small muted">${esc(d.fonte)}</p>${d.url?`<a class="btn sec sm" href="${esc(safeHref(d.url))}" target="_blank" rel="noopener">Abrir fonte oficial</a>`:''}<div class="doc-note" style="margin-top:10px">A nota abaixo é um instrumento de treino do CTI. Quando houver formulário oficial, o conteúdo deve ser conferido na versão vigente da fonte original.</div></div>${hist.length?`<div class="card"><h3>Histórico</h3><div class="doc-history">${hist.slice(-10).map(h=>`<span>${esc(h.data)} · ${f2Fmt(h.nota)}</span>`).join('')}</div></div>`:''}<div id="d2Resultado"></div></aside></div>`;
  $('#d2Voltar').onclick=()=>{box.hidden=true;$('#d2Grid').hidden=false;d2RenderHome();window.scrollTo({top:0,behavior:'smooth'})};
  $('#d2Limpar').onclick=()=>d.fields.forEach(f=>{const el=$('#d2_'+f[0]); if(el)el.value=''});
  $('#d2Corrigir').onclick=()=>d2Corrigir(d);
  window.scrollTo({top:0,behavior:'smooth'});
}
function d2Corrigir(d){
  let nota=0; const crit=[];
  d.fields.forEach(f=>{const v=($('#d2_'+f[0])?.value||'').trim(); const av=d2AvaliaCampo(d,f,v); nota+=f[3]*av.frac; const ok=av.frac>=.78; const parcial=av.frac>=.42&&!ok; let txt=''; if(ok)txt='Conteúdo consistente com os elementos esperados para este campo.'; else if(parcial)txt=`Parcial: faltam ou estão pouco explícitos ${av.miss.length?av.miss.join(', '):f[2]}.`; else txt=`Insuficiente. Procure registrar: ${f[2]}${av.miss.length?' Pontos ausentes: '+av.miss.join(', ')+'.':''}`; crit.push({ok,parcial,rot:f[1],txt});});
  nota=Math.round(Math.min(10,nota)*100)/100;
  D2H.push({id:d.id,data:hoje(),nota,seg:Math.round((Date.now()-D2.inicio)/1000)}); if(D2H.length>300)D2H=D2H.slice(-300); ls.set('cti_doc_treinos',D2H);
  const modelo=d.fields.map(f=>`${f[1]}\n${d.model[f[0]]||'—'}`).join('\n\n');
  $('#d2Resultado').innerHTML=`<div class="card doc-result"><span class="tiny muted">Resultado do treino</span><div class="doc-score" style="color:${f2Cor(nota)}">${f2Fmt(nota)}<span class="small muted"> / 10</span></div><p class="tiny muted">A correção combina estrutura, completude e conceitos-chave do caso. Compare sempre com o modelo de referência abaixo.</p><div class="doc-crit">${crit.map(c=>`<div><span class="${c.ok?'ok':'no'}">${c.ok?'✓':c.parcial?'△':'✕'}</span><span><b>${esc(c.rot)}</b><br><span class="muted">${esc(c.txt)}</span></span></div>`).join('')}</div></div><div class="card"><h3>${ic('book')}Modelo de referência</h3><div class="doc-model">${esc(modelo)}</div></div>`;
  d2Kpis(); $('#d2Resultado').scrollIntoView({behavior:'smooth',block:'start'});
}
function f2DocsRecomendados(e){
  const t=((e.titulo||'')+' '+(e.instrucoes||'')+' '+(e.area||'')).toLowerCase(); const out=[];
  if(/óbito|obito|morte|falec/.test(t))out.push('obito');
  if(/dengue|tuberc|viol[eê]ncia|mening|hepatite|sarampo|sífil|sifil/.test(t))out.push('sinan');
  if(/alta|intern|hospital|pneumonia|cirurg/.test(t))out.push('evolucao','resumo_alta');
  if(/encamin|refer|cardio|especialista/.test(t))out.push('encaminhamento');
  if(/prescre|receita|medica[çc][aã]o|tratamento/.test(t))out.push('receita');
  if(e.area==='Medicina de Família e Comunidade')out.push('soap');
  if(!out.length)out.push('soap','evolucao');
  return [...new Set(out)].slice(0,4);
}
function d2IrDeEstacao(id){
  clearInterval(F2S.h); F2S.est=null; F2S.fase=''; $('#f2Lista').hidden=false; $('#f2Est').hidden=true; f2MostrarTab('docs'); d2Abrir(id);
}


/* ============================================================ RADAR DE ATUALIZAÇÕES */
const UPD_AREAS = ['Todas','INEP / Revalida / ENAMED','SUS / Preventiva','Clínica Médica','Cardiologia','Endocrinologia / Diabetes','Pediatria','Ginecologia e Obstetrícia','Cirurgia','Infectologia','Dermatologia','Neurologia','Psiquiatria','Pneumologia','Nefrologia','Gastroenterologia','Emergência','Imunizações','Medicina de Família'];
let UPD_CACHE = ls.get('cti_atualizacoes_cache_v3', {}), UPD_DATA = null, UPD_BUSY = false;
function updKey(){ return `${$('#uArea')?.value || 'Todas'}|${$('#uDias')?.value || '120'}`; }
function fmtUpdData(s){ if (!s) return 'data não informada'; try { return new Date(s + 'T12:00:00').toLocaleDateString('pt-BR'); } catch { return s; } }
function updCacheSave(k, v){ UPD_CACHE[k] = v; const ks = Object.keys(UPD_CACHE); while (ks.length > 18) delete UPD_CACHE[ks.shift()]; try { ls.set('cti_atualizacoes_cache_v3', UPD_CACHE); } catch {} }
function renderUpdCards(){
  if (!UPD_DATA) return;
  const filtro = $('#uFonte').value;
  const itens = (UPD_DATA.items || []).filter(x => !filtro || x.fonte_nivel === filtro);
  $('#uStatus').textContent = `${itens.length} atualização(ões) · ${UPD_DATA.modo || 'busca'} · ${UPD_DATA.cache ? 'cache' : 'consulta atual'} · ${new Date(UPD_DATA.gerado_em || Date.now()).toLocaleString('pt-BR')}`;
  $('#uLista').innerHTML = itens.length ? `<div class="upd-grid">${itens.map(x => {
    const cls = x.fonte_nivel === 'oficial' ? 'src-oficial' : 'src-verificada';
    const level = x.fonte_rotulo || (x.fonte_nivel === 'oficial' ? 'Fonte oficial/primária' : 'Fonte secundária verificada');
    const high = (x.relevancia || '').toLowerCase().includes('alta prioridade');
    const ver = x.verificacao || (x.verificacao_parcial ? 'Verificação parcial' : '');
    return `<article class="upd-card">
      <div class="upd-meta"><span class="pill pri">${esc(x.area || 'Medicina')}</span><span class="pill">${esc(x.tipo || 'Atualização')}</span>${high ? '<span class="pill upd-high">Alta prioridade</span>' : ''}<span class="pill ${cls}">${esc(level)}</span>${ver ? `<span class="pill ${ver.includes('Secundária') ? 'src-partial' : ''}">${esc(ver)}</span>` : ''}</div>
      <h3>${esc(x.titulo)}</h3>
      <p>${esc(x.resumo)}</p>
      <div class="upd-meta"><span class="tiny muted">${ic('cal')} ${esc(fmtUpdData(x.data))}</span>${x.relevancia ? `<span class="tiny muted">· ${esc(x.relevancia)}</span>` : ''}${x.grounded === false ? '<span class="pill warn">Confirmar na fonte</span>' : ''}</div>
      <div class="upd-source"><div><b>${esc(x.fonte_nome || 'Fonte')}</b><span>${x.fonte_nivel === 'oficial' ? 'Publicação institucional/oficial' : 'Fonte secundária previamente verificada pelo CTi'}</span></div><a class="upd-link" href="${esc(safeHref(x.fonte_url))}" target="_blank" rel="noopener noreferrer">Abrir fonte ${ic('arrow')}</a></div>
    </article>`; }).join('')}</div>` : `<div class="card empty">Nenhuma atualização encontrada com este filtro. Isso não significa que não existam mudanças; tente outro período ou toque em “Atualizar agora”.</div>`;
  const canais = UPD_DATA.fontes_fixadas || [];
  if (canais.length){ $('#uCanais').hidden = false; $('#uCanaisLista').innerHTML = canais.map(x => `<a href="${esc(safeHref(x.url))}" target="_blank" rel="noopener noreferrer">${x.nivel === 'oficial' ? '✓' : '◐'} ${esc(x.nome)}</a>`).join(''); }
}
async function carregarAtualizacoes(force=false){
  if (UPD_BUSY) return;
  const k = updKey(), salvo = UPD_CACHE[k];
  if (!force && salvo){ UPD_DATA = salvo; renderUpdCards(); return; }
  UPD_BUSY = true; const b = $('#uAtualizar'); b.disabled = true;
  $('#uStatus').textContent = 'Buscando publicações recentes e verificando as fontes… isso pode levar alguns segundos.';
  try {
    const area = encodeURIComponent($('#uArea').value || 'Todas'), dias = $('#uDias').value || '120';
    const r = await api(`/api/atualizacoes?area=${area}&dias=${dias}&force=${force ? 'true' : 'false'}`);
    UPD_DATA = r;
    if ((r.items || []).length) updCacheSave(k, r);
    else { delete UPD_CACHE[k]; try { ls.set('cti_atualizacoes_cache_v3', UPD_CACHE); } catch {} }
    renderUpdCards();
    if (r.erro && !(r.items || []).length) toast('Nenhum resultado nesta tentativa; o CTi não vai guardar esse zero no cache.');
  } catch (e) {
    if (salvo){ UPD_DATA = salvo; renderUpdCards(); $('#uStatus').textContent = 'Sem conexão com a busca agora · exibindo a última consulta salva neste aparelho.'; }
    else { $('#uLista').innerHTML = `<div class="card empty">${ic('alert')} Não foi possível consultar agora: ${esc(e.message)}. Tente novamente mais tarde.</div>`; $('#uStatus').textContent = 'Busca indisponível temporariamente.'; }
  } finally { UPD_BUSY = false; b.disabled = false; }
}
function renderAtualizacoes(){
  if (!$('#uArea').options.length) {
    $('#uArea').innerHTML = UPD_AREAS.map(a => `<option value="${esc(a)}">${esc(a)}</option>`).join('');
    $('#uArea').onchange = () => { UPD_DATA = null; carregarAtualizacoes(false); };
    $('#uDias').onchange = () => { UPD_DATA = null; carregarAtualizacoes(false); };
    $('#uFonte').onchange = renderUpdCards;
    $('#uAtualizar').onclick = () => carregarAtualizacoes(true);
  }
  carregarAtualizacoes(false);
}

/* ============================================================ PERFIL, FAVORITOS e atalhos */
function aplicarPerfil(){
  const p = ls.get('cti_perfil', {});
  const nome = p.nome || 'Doutor(a)';
  $('#pfNome').textContent = nome; $('#saudNome').textContent = nome.replace(/^(Dra?\.?\s+)/i, '').split(' ')[0] || nome;
  $('#pfObj').textContent = p.obj || 'Definir objetivo';
  const ini = nome.replace(/^(Dra?\.?\s+)/i, '').split(/\s+/).filter(Boolean).map(w => w[0]).slice(0, 2).join('').toUpperCase();
  $('#pfAvatar').textContent = p.nome ? ini : 'Dr';
}
function editarPerfil(){
  const p = ls.get('cti_perfil', {});
  const nome = prompt('Como devemos te chamar? (ex.: Dr. Gabriel)', p.nome || ''); if (nome === null) return;
  const obj = prompt('Qual é o seu objetivo? (ex.: Revalida 2027 · R1 Clínica Médica)', p.obj || ''); if (obj === null) return;
  ls.set('cti_perfil', {nome:nome.trim(), obj:obj.trim()}); aplicarPerfil(); toast('Perfil atualizado!');
}
const favs = () => ls.get('cti_favoritos', []);
function toggleFav(id){ let f = favs(); f = f.includes(id) ? f.filter(x => x !== id) : [...f, id]; ls.set('cti_favoritos', f); toast(f.includes(id) ? 'Adicionada aos favoritos' : 'Removida dos favoritos'); return f.includes(id); }
const favBtn = id => { const on = favs().includes(id); return `<button class="fav ${on ? 'on' : ''}" data-fav="${id}" title="Favoritar">${ic('star')}<span>${on ? 'Favorita' : 'Favoritar'}</span></button>`; };
function ligarFav(cont){ $$(cont + ' [data-fav]').forEach(b => b.onclick = e => { e.stopPropagation(); const on = toggleFav(+b.dataset.fav); b.classList.toggle('on', on); b.querySelector('span').textContent = on ? 'Favorita' : 'Favoritar'; }); }
window.abrirBanco = (f = {}) => {
  $('#bExame').value = f.exame || ''; $('#bExame').onchange(); $('#bEdicao').value = '';
  $('#bArea').value = f.area || ''; $('#bArea').onchange(); $('#bAno').value = f.ano || ''; $('#bBusca').value = '';
  $('#bLista').innerHTML = '';
  mostrar('banco');   // lista vazia -> busca com os filtros acima
};
async function renderFavoritos(){
  const ids = favs();
  $('#fvInfo').textContent = `${ids.length} questão(ões) favoritada(s)`;
  if (!ids.length){ $('#fvLista').innerHTML = `<div class="card empty">${ic('star')} Nenhuma favorita ainda. Toque na estrela de uma questão para guardá-la aqui.</div>`; return; }
  const r = {items: await carregarLote(ids.slice(-100))};
  const resp = {}; HIST.forEach(h => resp[h.id] = h.acertou);
  $('#fvLista').innerHTML = r.items.map(q => itemHtml(q, resp)).join('');
  ligarItens('#fvLista', 'favoritos');
}
$('#fvTreinar').onclick = async () => { const ids = favs(); if (!ids.length) return toast('Nenhuma favorita ainda.');
  const r = {items: await carregarLote(ids.slice(-100))};
  iniciarSessao({tipo:'banco', titulo:`Favoritos (${r.items.length})`, sub:'Suas questões favoritas', questoes:r.items, modo:'treino', onFim:() => mostrar('favoritos')}); };

/* ============================================================ PAINEL (início) */
const FOCOS = [{k:'', rot:'Banco de Questões', ic:'banco'}, {k:'Revalida INEP', rot:'Revalida', ic:'map'}, {k:'ENAMED', rot:'ENAMED', ic:'doc'},
  {k:'CONAREM', rot:'CONAREM', ic:'cap'}, {k:'USMLE Step 2 CK', rot:'USMLE', ic:'steth'}];
function renderPainel(){
  if (!STATS) return;
  const FOC = FOCOS.filter(f => !f.k || STATS.por_exame[f.k]);   // exames ocultos (CTI_OCULTAR_EXAMES) somem da tela
  $('#bResumo').textContent = `${STATS.total_questoes.toLocaleString('pt-BR')} questões oficiais: ` + FOC.slice(1).map(f => `${f.rot} ${STATS.por_exame[f.k] || 0}`).join(' · ') + '. Gabarito oficial e IA tutora em todas.';
  $('#pnFoco').innerHTML = FOC.map((f, i) => `<button class="${i === 0 ? 'on' : ''}" data-ex="${esc(f.k)}">${ic(f.ic)}<span>${f.rot}</span><small>${(f.k ? STATS.por_exame[f.k] || 0 : STATS.total_questoes).toLocaleString('pt-BR')} questões</small></button>`).join('');
  $$('#pnFoco button').forEach(b => b.onclick = () => abrirBanco({exame:b.dataset.ex}));
  const P = +$('#pnPer').value, desde = P ? addDias(hoje(), -(P - 1)) : '0000-00-00';
  const l = HIST.filter(h => h.data >= desde), n = l.length, ac = l.filter(h => h.acertou).length, p = pct(ac, n);
  const pend = ERROS.filter(e => !e.dominada).length;
  // gauge 270°
  const C = 2 * Math.PI * 60, arc = .75 * C;
  $('#pnEvol').innerHTML = `<div class="gauge-wrap"><div class="gauge"><svg viewBox="0 0 150 150" width="150" height="150">
      <circle cx="75" cy="75" r="60" fill="none" stroke="var(--card2)" stroke-width="12" stroke-linecap="round" stroke-dasharray="${arc} ${C}" transform="rotate(135 75 75)"/>
      <circle cx="75" cy="75" r="60" fill="none" stroke="url(#gGauge)" stroke-width="12" stroke-linecap="round" stroke-dasharray="${Math.max(.001, arc * p / 100)} ${C}" transform="rotate(135 75 75)"/>
      <defs><linearGradient id="gGauge" x1="0" x2="1"><stop offset="0" stop-color="#8E0710"/><stop offset="1" stop-color="#E10613"/></linearGradient></defs></svg>
      <div class="v"><b>${n ? p + '%' : '–'}</b><span>Taxa de acerto</span></div></div>
    <div class="ev-list">
      <div><span class="dot" style="background:var(--navy)">${ic('check')}</span><b>${n.toLocaleString('pt-BR')}</b>Questões respondidas</div>
      <div><span class="dot" style="background:var(--ok)">${ic('check')}</span><b>${ac.toLocaleString('pt-BR')}</b>Acertos (${p}%)</div>
      <div><span class="dot" style="background:var(--red)">${ic('x')}</span><b>${(n - ac).toLocaleString('pt-BR')}</b>Erros (${n ? 100 - p : 0}%)</div>
      <div><span class="dot" style="background:var(--gray)">${ic('clock')}</span><b>${pend}</b>Pendentes de revisão</div>
    </div></div>
    ${n ? '' : `<p class="small muted" style="margin:10px 0 0">Responda questões para acompanhar sua evolução aqui.</p>`}`;
  // especialidades
  const porArea = agrega(l, 'area');
  $('#pnEsp').innerHTML = STATS.especialidades.map(a => { const s = porArea[a], v = s ? pct(s.a, s.n) : 0;
    return `<div class="esp-row" data-train-area="${esc(a)}" title="Treinar ${esc(a)}">${ic(AREA_ICON[a] || 'steth')}<span>${esc(a)}</span><div class="progress"><i style="width:${v}%"></i></div><b>${s ? v + '%' : '–'}</b></div>`; }).join('');
  renderLinha(l, P);
  renderMetas();
  if (!DQ) carregarDestaque(); else renderDestaque();
}
$('#pnPer').onchange = renderPainel;
function renderLinha(l, P){
  const semanal = !P || P > 31;
  let pts;
  if (!semanal) pts = Array.from({length:P}, (_, i) => { const d = addDias(hoje(), i - P + 1), x = l.filter(h => h.data === d); return {lab:d.slice(8) + '/' + d.slice(5, 7), n:x.length, v:pct(x.filter(h => h.acertou).length, x.length)}; });
  else { const W = P ? Math.ceil(P / 7) : 12; pts = Array.from({length:W}, (_, i) => { const k = W - 1 - i, ini = addDias(hoje(), -7 * k - 6), fim = addDias(hoje(), -7 * k), x = l.filter(h => h.data >= ini && h.data <= fim);
    return {lab:fim.slice(8) + '/' + fim.slice(5, 7), n:x.length, v:pct(x.filter(h => h.acertou).length, x.length)}; }); }
  const com = pts.map((p, i) => ({...p, i})).filter(p => p.n);
  if (!com.length){ $('#pnLinha').innerHTML = '<div class="empty small" style="padding:20px">Sua curva de acertos aparece aqui depois das primeiras questões.</div>'; return; }
  const W = 420, H = 150, L = 30, T = 12, B = 22, w = W - L - 10, h = H - T - B, X = i => L + (pts.length === 1 ? w / 2 : w * i / (pts.length - 1)), Y = v => T + h - h * v / 100;
  const linha = com.map(p => `${X(p.i).toFixed(1)},${Y(p.v).toFixed(1)}`).join(' ');
  const area = `${X(com[0].i)},${T + h} ${linha} ${X(com[com.length - 1].i)},${T + h}`;
  const ult = com[com.length - 1], step = Math.ceil(pts.length / 6);
  $('#pnLinha').innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Evolução da taxa de acerto">
    <defs><linearGradient id="gLin" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="#E10613" stop-opacity=".28"/><stop offset="1" stop-color="#E10613" stop-opacity="0"/></linearGradient></defs>
    ${[0, 25, 50, 75, 100].map(v => `<line x1="${L}" x2="${W - 10}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--bd)" stroke-dasharray="${v ? '3 4' : ''}"/><text x="${L - 6}" y="${Y(v) + 3}" font-size="9" text-anchor="end" fill="var(--mut)">${v}%</text>`).join('')}
    ${pts.map((p, i) => i % step === 0 || i === pts.length - 1 ? `<text x="${X(i)}" y="${H - 6}" font-size="9" text-anchor="${i === pts.length - 1 ? 'end' : i === 0 ? 'start' : 'middle'}" fill="var(--mut)">${p.lab}</text>` : '').join('')}
    <polygon points="${area}" fill="url(#gLin)"/>
    <polyline points="${linha}" fill="none" stroke="#E10613" stroke-width="2.2" stroke-linejoin="round"/>
    ${com.map(p => `<circle cx="${X(p.i)}" cy="${Y(p.v)}" r="3" fill="var(--card)" stroke="#E10613" stroke-width="2"><title>${p.lab}: ${p.v}% (${p.n} questões)</title></circle>`).join('')}
    <g transform="translate(${Math.min(X(ult.i), W - 40) - 22},${Math.max(Y(ult.v) - 34, 0)})"><rect width="44" height="24" rx="5" fill="var(--card)" stroke="var(--bd)"/><text x="22" y="11" font-size="10" font-weight="700" text-anchor="middle" fill="currentColor">${ult.v}%</text><text x="22" y="20" font-size="7.5" text-anchor="middle" fill="var(--mut)">${ult.lab}</text></g>
  </svg><div class="tiny muted">${semanal ? 'Taxa de acerto por semana' : 'Taxa de acerto por dia'} · passe o mouse nos pontos para ver o número de questões.</div>`;
}
function renderMetas(){
  const m = Object.assign({questoes:600, acerto:80, simulados:4}, ls.get('cti_metas', {}));
  const mes = hoje().slice(0, 7), l = HIST.filter(h => h.data.startsWith(mes)), ac = pct(l.filter(h => h.acertou).length, l.length);
  const sims = ls.get('cti_simulados', []).filter(s => s.data.startsWith(mes)).length;
  const card = (icn, v, rot, prog, meta) => `<div class="meta"><div class="top">${ic(icn)}<div><b>${v}</b><small>${rot}</small></div></div>
    <div class="row"><div class="progress"><i style="width:${Math.min(100, prog)}%"></i></div><span>${Math.min(999, prog)}%</span></div><small style="margin-top:4px">Meta: ${meta}</small></div>`;
  $('#pnMetas').innerHTML = card('target', l.length.toLocaleString('pt-BR'), 'Questões no mês', pct(l.length, m.questoes), m.questoes.toLocaleString('pt-BR')) +
    card('trophy', l.length ? ac + '%' : '–', 'Taxa de acerto', pct(ac, m.acerto), m.acerto + '%') +
    card('cal', sims, 'Simulados completos', pct(sims, m.simulados), m.simulados);
}
$('#pnMetasCfg').onclick = () => {
  const m = Object.assign({questoes:600, acerto:80, simulados:4}, ls.get('cti_metas', {}));
  const q = prompt('Meta de questões por mês:', m.questoes); if (q === null) return;
  const a = prompt('Meta de taxa de acerto (%):', m.acerto); if (a === null) return;
  const s = prompt('Meta de simulados completos por mês:', m.simulados); if (s === null) return;
  ls.set('cti_metas', {questoes:Math.max(1, +q || 600), acerto:Math.min(100, Math.max(1, +a || 80)), simulados:Math.max(1, +s || 4)}); renderMetas(); toast('Metas atualizadas!');
};
/* ---------- questão em destaque (uma por dia) */
let DQ = null;
async function carregarDestaque(){
  const d = hoje(), salvo = ls.get('cti_destaque', {});
  let id = salvo.data === d ? salvo.id : null;
  if (!id){ const pool = INDICE.filter(i => i.valida && !i.dup && !i.img); let h = 0; for (const c of d + semente()) h = (h * 31 + c.charCodeAt(0)) >>> 0;
    id = pool[h % pool.length].id; ls.set('cti_destaque', {data:d, id}); }
  try { const q = await api('/api/questoes/' + id); DQ = {q, resp:salvo.data === d ? salvo.resp || null : null, exp:null};
    renderDestaque(); if (DQ.resp){ DQ.exp = await buscarIA(id, q._access || '', DQ.resp.answer_token || ''); renderDestaque(); } }
  catch { $('#pnDestaque').innerHTML = '<div class="empty small">Não foi possível carregar a questão.</div>'; }
}
function renderDestaque(){
  if (!DQ) return;
  const {q, resp} = DQ, alts = LETRAS.filter(l => q['alt_' + l]);
  const fav = favs().includes(q.id); $('#dqFav').classList.toggle('on', fav); $('#dqFav').lastChild.textContent = fav ? 'Favorita' : 'Favoritar';
  $('#pnDestaque').innerHTML = `<div class="row" style="gap:6px"><span class="pill pri">${ic('flag')}${esc(q.exame)}</span><span class="pill">${esc(q.especialidade)}</span><span class="pill pur">${esc(q.edicao)} · Q${q.numero}</span></div>
    <div class="destaque"><div><div class="q-enun">${esc(q.enunciado)}</div>
      <div id="dqAlts">${alts.map(l => { let c = ''; if (resp){ if (l === resp.gab || resp.gab === 'ANULADA') c = 'certa'; else if (l === resp.sel) c = 'errada'; }
        return `<button class="alt ${c}" data-l="${l}" ${resp ? 'disabled' : ''}><span class="lt">${l}</span><span class="tx">${esc(q['alt_' + l])}</span></button>`; }).join('')}</div></div>
      <div class="resp-box ${resp ? '' : 'wait'}">${resp ? `<b class="t">${ic('bulb')}Resposta correta: ${resp.gab}</b>
        <div style="margin-bottom:6px;font-weight:700;color:${resp.acertou ? 'var(--ok)' : 'var(--bad)'}">${resp.acertou ? 'Você acertou!' : `Você marcou ${resp.sel}.`}</div>
        <div>${esc(!DQ.exp ? 'Carregando a explicação da IA…' : DQ.exp.fonte === 'ia' ? DQ.exp.porque_correta : 'Comentário da IA indisponível agora (limite diário ou sem conexão).')}</div>
        <button class="btn sm" style="margin-top:10px" id="dqCompleta">Ver explicação completa ${ic('arrow')}</button>`
      : `<b class="row" style="gap:6px;color:var(--tx)">${ic('bulb')}Sua vez!</b><div style="margin-top:4px">Escolha uma alternativa para ver a resposta correta e a explicação da IA tutora. Uma nova questão aparece a cada dia.</div>`}</div></div>`;
  $$('#dqAlts .alt').forEach(b => b.onclick = () => responderDestaque(b.dataset.l));
  $('#dqCompleta') && ($('#dqCompleta').onclick = verDestaque);
}
async function responderDestaque(sel){
  if (!DQ || DQ.resp) return;
  const r = await post('/api/responder', {question_id:DQ.q.id, selected_option:sel, access_token:DQ.q._access || ''});
  DQ.resp = {sel, acertou:r.is_correct, gab:r.gabarito_oficial, answer_token:r.answer_token || ''}; DQ.exp = r.explicacao_ia || null; if (DQ.exp) iaGuardar(DQ.q.id, DQ.exp);
  registrar(DQ.q, r.is_correct, 'destaque');
  ls.set('cti_destaque', {data:hoje(), id:DQ.q.id, resp:DQ.resp});
  renderPainel();
  if (!DQ.exp){ DQ.exp = await buscarIA(DQ.q.id, DQ.q._access || '', DQ.resp.answer_token || ''); renderDestaque(); }
}
function verDestaque(){
  if (!DQ) return;
  const q = DQ.q, cfg = {tipo:'banco', titulo:'Questão em destaque', sub:`${q.edicao} · Q${q.numero}`, questoes:[q], modo:'treino', onFim:() => mostrar('painel'),
    onResposta:(qq, sel, acertou, gab, answerToken) => { DQ.resp = {sel, acertou, gab, answer_token:answerToken || ''}; ls.set('cti_destaque', {data:hoje(), id:q.id, resp:DQ.resp}); }};
  if (DQ.resp){ cfg.respostas = {[q.id]:DQ.resp}; if (DQ.exp && DQ.exp.fonte === 'ia') cfg.explic = {[q.id]:DQ.exp}; }
  iniciarSessao(cfg);
}
$('#dqVer').onclick = verDestaque;
$('#dqFav').onclick = () => { if (!DQ) return; const on = toggleFav(DQ.q.id); $('#dqFav').classList.toggle('on', on); $('#dqFav').lastChild.textContent = on ? 'Favorita' : 'Favoritar'; };


document.addEventListener('click', e => {
  const el = e.target.closest('[data-train-area],[data-train-tema],[data-open-reforco],[data-open-reforco-area]');
  if (!el) return;
  if (el.matches('a')) e.preventDefault();
  if (el.dataset.trainArea !== undefined) window.treinarArea(el.dataset.trainArea || '', '');
  else if (el.dataset.trainTema !== undefined) window.treinarArea('', el.dataset.trainTema || '');
  else if (el.dataset.openReforcoArea !== undefined) window.abrirReforco('', el.dataset.openReforcoArea || '');
  else if (el.dataset.openReforco !== undefined) window.abrirReforco(el.dataset.openReforco || '', '');
});


init().catch(e => { document.querySelector('main').innerHTML = `<div class="card">Erro ao carregar: ${esc(e.message)}</div>`; });
