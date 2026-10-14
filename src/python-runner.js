let dispose = () => {};
export function disposePythonRunner() { dispose(); dispose = () => {}; }
export function mountPythonRunner(root, source, allowed = true) {
  disposePythonRunner();
  root.innerHTML=`<details class="python-lab"><summary>Запустить Python в браузере</summary><p>Код выполняется на вашем устройстве, не на сервере. Доступны стандартная библиотека и ввод ниже. Изменения редактора не меняют файл.</p><label for="python-code">Код Python</label><textarea id="python-code" rows="12" spellcheck="false"></textarea><label for="python-input">Входные данные · по одной строке</label><textarea id="python-input" rows="3" placeholder="Данные для input()"></textarea><div class="python-actions"><button id="python-run" class="button primary">Запустить</button><button id="python-stop" class="button secondary" disabled>Остановить</button></div><p id="python-status" role="status"></p><pre id="python-output" aria-label="Результат Python"></pre><small>Выполнение до 10 секунд; вывод до 64 КБ. Сторонние пакеты и доступ к файлам VPS не поддерживаются.</small></details>`;
  const editor=root.querySelector('#python-code'),input=root.querySelector('#python-input');editor.value=source;
  const run=root.querySelector('#python-run'),stop=root.querySelector('#python-stop'),status=root.querySelector('#python-status'),output=root.querySelector('#python-output');
  let frame=null,timer=null,listener=null,started=false;
  const cleanup=()=>{clearTimeout(timer);if(listener)window.removeEventListener('message',listener);frame?.remove();frame=null;listener=null;run.disabled=!allowed;stop.disabled=true;};
  dispose=cleanup;
  if(!allowed){run.disabled=true;status.textContent='Запуск доступен для файлов Python до 100 КБ. Предпросмотр остаётся доступным.';}
  stop.onclick=()=>{cleanup();status.textContent='Выполнение остановлено';};
  run.onclick=()=>{
    cleanup();started=false;if(editor.value.length>100000||input.value.length>20000){status.textContent='Слишком большой код или ввод: до 100 КБ кода и 20 КБ ввода.';return;}run.disabled=true;stop.disabled=false;output.textContent='';status.textContent='Подготовка Python…';
    const runtime=new URL(`${import.meta.env.BASE_URL}python-runtime/`,location.origin).href;
    const channel=crypto.randomUUID();const nonce=crypto.randomUUID();
    const workerCode=`let python;let outputSize=0;function emit(text){text=String(text);if(outputSize>=65536)return;const part=text.slice(0,65536-outputSize);outputSize+=part.length;postMessage({type:'output',text:part+'\\n'});if(outputSize>=65536)postMessage({type:'output',text:'\\n[Вывод ограничен 64 КБ]'});}onmessage=async(event)=>{try{importScripts(${JSON.stringify(runtime+'pyodide.js')});python=await loadPyodide({indexURL:${JSON.stringify(runtime)}});python.setStdout({batched:emit});python.setStderr({batched:emit});const lines=event.data.input?event.data.input.split('\\n'):[];python.setStdin({stdin:()=>lines.length?lines.shift():null});postMessage({type:'ready'});await python.runPythonAsync(event.data.code);postMessage({type:'done'});}catch(error){postMessage({type:'error',text:String(error).slice(0,8192)});}};`;
    const bridge=`const channel=${JSON.stringify(channel)};const worker=new Worker(URL.createObjectURL(new Blob([${JSON.stringify(workerCode)}],{type:'text/javascript'})));worker.onmessage=event=>{const message=event.data;if(['ready','output','done','error'].includes(message?.type))parent.postMessage({channel,type:message.type,text:typeof message.text==='string'?message.text.slice(0,65536):''},'*');};worker.onerror=()=>parent.postMessage({channel,type:'error',text:'Не удалось загрузить среду Python'},'*');worker.postMessage(${JSON.stringify({code:editor.value,input:input.value}).replaceAll('<','\\u003c')});`;
    frame=document.createElement('iframe');frame.hidden=true;frame.setAttribute('sandbox','allow-scripts');frame.setAttribute('referrerpolicy','no-referrer');
    // Opaque origin + worker-inherited CSP prevent Python/JS from reaching private cloud APIs.
    frame.srcdoc=`<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'nonce-${nonce}' 'wasm-unsafe-eval' blob: ${runtime}; connect-src ${runtime}; worker-src blob:; child-src blob:"><script nonce="${nonce}">${bridge.replaceAll('</script','<\\/script')}</script>`;
    listener=event=>{
      if(event.source!==frame?.contentWindow||event.data?.channel!==channel)return;
      const {type,text}=event.data;
      if(type==='ready'&&!started){started=true;clearTimeout(timer);status.textContent='Выполнение…';timer=setTimeout(()=>{cleanup();status.textContent='Остановлено: превышено 10 секунд';},10000);}
      if(type==='output')output.textContent=(output.textContent+String(text)).slice(0,66000);
      if(type==='done'){cleanup();status.textContent='Выполнение завершено';if(!output.textContent)output.textContent='Программа завершилась без вывода';}
      if(type==='error'){cleanup();status.textContent='Ошибка Python';output.textContent=(output.textContent+'\n'+String(text)).slice(0,66000);}
    };
    window.addEventListener('message',listener);document.body.append(frame);
    timer=setTimeout(()=>{cleanup();status.textContent='Не удалось подготовить Python за 90 секунд. Проверьте соединение и повторите.';},90000);
  };
}
