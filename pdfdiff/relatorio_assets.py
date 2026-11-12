"""Recursos do relatório HTML: CSS, JavaScript e ícones.

Ficam em constantes porque o relatório é um arquivo único, que abre sem rede (o servidor o entrega com uma CSP
que bloqueia tudo que vem de fora). Os tokens de cor do CSS têm os mesmos nomes e valores de static/style.css,
e um teste confere que não divergem.
"""


# Estilo do relatório (minificado). Claro e escuro pelo `prefers-color-scheme`; o movimento só vale com
# `prefers-reduced-motion: no-preference`. Os blocos seguem a ordem: tokens, base, componentes, tela estreita.
CSS = """
:root{color-scheme:light dark;
--fundo:#f5f5f8;--superficie:#fff;--superficie-2:#ececf2;--linha:#dcdde6;--linha-forte:#737689;--texto:#15161d;--texto-2:#454859;
--acento:#4338ca;--acento-hover:#362ba3;--acento-suave:#e8e7fc;--sobre-acento:#fff;
--ok-fundo:#d6f3df;--ok-texto:#0a5427;--erro-fundo:#fde2e2;--erro-texto:#8c1a1a;--aviso-fundo:#fcebc2;--aviso-texto:#6a4000;
--esp-1:.25rem;--esp-2:.5rem;--esp-3:.75rem;--esp-4:1rem;--esp-5:1.5rem;--esp-6:2rem;--esp-7:3rem;--esp-8:4rem;
--raio-1:6px;--raio-2:8px;--raio-3:12px;--raio-4:16px;
--fonte:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
--fonte-mono:ui-monospace,"SF Mono","Cascadia Mono",Consolas,Menlo,monospace;
--t-pequeno:.875rem;--t-base:1rem;--t-medio:1.125rem;--t-grande:1.75rem;--altura-linha:1.55;
--mov-1:120ms;--mov-2:220ms;--curva:cubic-bezier(.2,.8,.2,1)}
@media(prefers-color-scheme:dark){:root{
--fundo:#0f1015;--superficie:#17181f;--superficie-2:#20222c;--linha:#2c2e3a;--linha-forte:#868a9e;--texto:#ededf2;--texto-2:#b9bccb;
--acento:#a3a8ff;--acento-hover:#bcc0ff;--acento-suave:#24264a;--sobre-acento:#0f1015;
--ok-fundo:#123d27;--ok-texto:#a7efc1;--erro-fundo:#4a1d22;--erro-texto:#ffc4c9;--aviso-fundo:#46330a;--aviso-texto:#ffdf9a}}
*,*::before,*::after{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--fundo);color:var(--texto);font:var(--t-base)/var(--altura-linha) var(--fonte);-webkit-text-size-adjust:100%}
main{max-width:54rem;margin:0 auto;padding:var(--esp-6) var(--esp-4) var(--esp-8)}
h1,h2{margin:0;font-weight:700;line-height:1.15;letter-spacing:-.015em;text-wrap:balance}
h1{font-size:var(--t-grande)}h2{margin:var(--esp-7) 0 var(--esp-2);font-size:var(--t-medio)}
p{margin:0;text-wrap:pretty}i,em,cite{font-style:normal}
.num{font-variant-numeric:tabular-nums}.mono{font-family:var(--fonte-mono);font-size:.9em}
:focus-visible{outline:3px solid var(--acento);outline-offset:2px}
.ic{flex:none;width:1.125rem;height:1.125rem;fill:none;stroke:currentColor;stroke-width:1.75;stroke-linecap:round;stroke-linejoin:round}
.nota{margin-top:var(--esp-3);color:var(--texto-2);font-size:var(--t-pequeno)}
.doc{margin-bottom:var(--esp-1);color:var(--texto-2);font-weight:600;overflow-wrap:anywhere;text-wrap:balance}
.arqs{display:flex;flex-wrap:wrap;align-items:center;gap:var(--esp-2) var(--esp-3);margin-top:var(--esp-3);color:var(--texto-2)}
.arq{display:inline-flex;flex-wrap:wrap;align-items:center;gap:var(--esp-1) var(--esp-2)}
.nome{color:var(--texto);font-weight:600;overflow-wrap:anywhere}
.filtros{position:sticky;top:0;z-index:2;display:flex;flex-wrap:wrap;align-items:center;gap:var(--esp-2) var(--esp-3);margin-top:var(--esp-5);padding:var(--esp-3) 0;background:var(--fundo);border-bottom:1px solid var(--linha)}
.seg{--recuo:4px;display:inline-flex;flex-wrap:wrap;gap:2px;padding:var(--recuo);background:var(--superficie-2);border-radius:var(--raio-3)}
.seg button{display:inline-flex;align-items:center;gap:var(--esp-2);min-height:2.5rem;padding:0 var(--esp-3);font:inherit;font-size:var(--t-pequeno);font-weight:600;color:var(--texto-2);background:transparent;border:0;border-radius:calc(var(--raio-3) - var(--recuo));cursor:pointer}
.seg button:hover{background:var(--superficie)}
.seg button.on{color:var(--sobre-acento);background:var(--acento)}
.seg .num{font-weight:500}
.seg .alerta .num{padding:0 var(--esp-2);color:var(--aviso-texto);background:var(--aviso-fundo);border-radius:var(--raio-1)}
.total{margin-left:auto;color:var(--texto-2);font-size:var(--t-pequeno)}
.item{margin-top:var(--esp-4);overflow:hidden;background:var(--superficie);border:1px solid var(--linha);border-radius:var(--raio-3)}
.item header{display:flex;flex-wrap:wrap;align-items:center;gap:var(--esp-1) var(--esp-4);padding:var(--esp-3) var(--esp-4);color:var(--texto-2);font-size:var(--t-pequeno);border-bottom:1px solid var(--linha)}
.n{font-weight:600;font-variant-numeric:tabular-nums}
.tipo{display:inline-flex;align-items:center;gap:var(--esp-1);font-weight:600}
.t-adicionado{color:var(--ok-texto)}.t-removido{color:var(--erro-texto)}.t-modificado{color:var(--acento)}
.pags{font-variant-numeric:tabular-nums}
.flag{font-weight:600}.f-sem{padding:0 var(--esp-2);color:var(--aviso-texto);background:var(--aviso-fundo);border-radius:var(--raio-1)}
.item p{padding:var(--esp-4);font-size:var(--t-medio);line-height:1.65;overflow-wrap:anywhere}
ins{padding:0 .2em;color:var(--ok-texto);background:var(--ok-fundo);text-decoration:none;border-radius:3px}
del{padding:0 .2em;color:var(--erro-texto);background:var(--erro-fundo);text-decoration:line-through;text-decoration-thickness:.08em;border-radius:3px}
.ver{display:inline-flex;align-items:center;gap:var(--esp-1);min-height:2.25rem;margin-left:auto;padding:0 var(--esp-3) 0 var(--esp-2);color:var(--acento);font-weight:600;text-decoration:none;background:var(--superficie);border:1px solid var(--linha);border-radius:var(--raio-2)}
.ver:hover{background:var(--acento-suave)}
.vazio{margin-top:var(--esp-5);color:var(--texto-2)}
details{margin-top:var(--esp-3)}summary{color:var(--acento);font-weight:600;cursor:pointer}
details p,dl{margin-top:var(--esp-2);color:var(--texto-2)}dt{margin-top:var(--esp-3);color:var(--texto);font-weight:600}dd{margin:0}
ul.mono{padding-left:var(--esp-5)}
@keyframes entra{from{opacity:0;transform:translateY(6px)}}
@media(prefers-reduced-motion:no-preference){.item.novo{animation:entra var(--mov-2) var(--curva)}.seg button,.ver{transition:background-color var(--mov-1)}}
@media(max-width:640px){.arqs{flex-direction:column;align-items:flex-start;gap:var(--esp-1)}.arqs>.ic{display:none}.filtros{position:static}.seg{width:100%}.seg button{flex:1 1 auto;justify-content:center}.total{margin-left:0}}
"""


# Comportamento do relatório: os filtros por tipo (data-ft) e por marcação em cor (data-fm), a contagem e o
# aviso de lista vazia. Quando o relatório é aberto pelo servidor (/comparacoes/<id>/relatorio), liga também o
# link «Ver nos PDFs» de cada card, com as páginas de data-pb (base) e data-pr (revisada); no arquivo baixado ou
# gerado pela linha de comando o endereço não casa e o link continua escondido.
JS = """
const itens=[...document.querySelectorAll('.item')];let ft='todos',fm='marcado',ini=true;
function aplica(){let n=0;itens.forEach(e=>{const ok=(ft==='todos'||e.dataset.tipo===ft)&&(fm==='todas'||e.dataset.marc===fm);const antes=e.hidden;e.hidden=!ok;
if(ok){n++;if(!ini&&antes)e.classList.add('novo')}else e.classList.remove('novo')});
document.getElementById('n').textContent=n;document.getElementById('vazio').hidden=n>0;ini=false}
function liga(attr,set){const bs=[...document.querySelectorAll('['+attr+']')];
bs.forEach(b=>b.onclick=()=>{set(b.getAttribute(attr));bs.forEach(x=>{x.classList.toggle('on',x===b);x.setAttribute('aria-pressed',x===b)});aplica()})}
liga('data-ft',v=>ft=v);liga('data-fm',v=>fm=v);aplica();
const srv=location.pathname.match(/^\\/comparacoes\\/([0-9a-f]{32})\\/relatorio$/);
if(srv)itens.forEach(e=>{const q=[];if(e.dataset.pb)q.push('b='+e.dataset.pb);if(e.dataset.pr)q.push('r='+e.dataset.pr);
const a=e.querySelector('.ver');if(a&&q.length){a.href='/comparacoes/'+srv[1]+'/pdfs?'+q.join('&');a.hidden=false}});
"""


def _icone(corpo):
    """Monta um ícone SVG embutido a partir dos traços (`corpo`).

    O relatório não pode carregar nada de fora, então os ícones são um só conjunto, com traço de 1,75 e pontas
    redondas.
    """
    return "<svg class='ic' viewBox='0 0 24 24' aria-hidden='true'>%s</svg>" % corpo


# Os ícones do relatório, por nome: arquivo e seta (resumo), tipo da diferença e «lado a lado» (link para os PDFs)
ICONES = {
    "arquivo": _icone("<path d='M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z'/><path d='M14 3v5h5'/>"),
    "seta": _icone("<path d='M5 12h14'/><path d='m13 6 6 6-6 6'/>"),
    "adicionado": _icone("<path d='M12 5v14M5 12h14'/>"),
    "removido": _icone("<path d='M5 12h14'/>"),
    "modificado": _icone("<path d='M4 20l1-4L16 5a2.1 2.1 0 0 1 3 3L8 19z'/><path d='m14 7 3 3'/>"),
    "lado_a_lado": _icone("<rect x='3' y='4' width='18' height='16' rx='2'/><path d='M12 4v16'/>"),
}
