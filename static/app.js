const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="local-token"]').content;
let courses = [], current = null, source = 'demo', timer, dataset = 0, automationActive = false;
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(timer); timer = setTimeout(() => $('toast').hidden = true, 11000); }
async function api(path, data = {}) {
  const response = await fetch('/api/' + path, {method:'POST', headers:{'Content-Type':'application/json','X-Local-Token':token}, body:JSON.stringify(data)});
  const result = await response.json(); if (!response.ok) throw Error(result.error || '请求失败'); return result;
}
async function busy(button, action) { button.disabled = true; try { await action(); } catch(e) { toast(e.message); } finally { button.disabled = false; } }
function el(tag, text, cls) { const node = document.createElement(tag); if (text != null) node.textContent = text; if (cls) node.className = cls; return node; }
function demo() {
  setCourses([{id:'demo-1',name:'示例 · 经济学的日常',teacher:'演示课程',chapters:[{name:'第一章　从选择开始',children:[{name:'1.1 什么是机会成本',duration:'12:30'},{name:'1.2 理解边际效用',duration:'18:20'}]},{name:'第二章　市场与价格',children:[{name:'2.1 供给和需求',duration:'16:45'},{name:'2.2 价格如何形成',duration:'21:10'}]}]},{id:'demo-2',name:'示例 · 人工智能导论',teacher:'演示课程',chapters:[{name:'第一章　认识人工智能',children:[{name:'1.1 从规则到学习',duration:'15:00'},{name:'1.2 机器学习的基本概念',duration:'20:00'}]}]}], 'demo');
}
function setCourses(items, kind) {
  courses = items; source = kind; dataset++;
  $('readWarnings').hidden = true;
  $('mode').textContent = kind === 'demo' ? '演示数据' : kind === 'live' ? '平台返回数据' : '本地导入';
  $('modeText').textContent = kind === 'demo' ? '当前展示示例课程，不代表你的真实课程或学习进度。' : kind === 'live' ? '通过本地适配器读取；学习请在原平台完成。' : '已导入课程快照；刷新页面后需重新导入。';
  selectCourse(items[0] || null);
}
function selectCourse(course) {
  current = course; render();
  if (source === 'live' && current && !current._loaded && !current._loading) loadChapters(current);
}
async function loadChapters(course) {
  if (!course || course._loading || source !== 'live') return;
  const revision = dataset;
  course._loading = true; course._error = '';
  if (current === course) render();
  try {
    const result = await api('chapters',{course_id:course.id});
    if (dataset !== revision) return;
    course.chapters = result.chapters; course._loaded = true;
  } catch(error) {
    if (dataset !== revision) return;
    course._error = error.message; toast(error.message);
  } finally {
    course._loading = false;
    if (dataset === revision && current === course) render();
  }
}
function render() {
  $('courses').replaceChildren(); $('count').textContent = courses.length + ' 门';
  for (const course of courses) {
    const button = el('button',null,'course' + (course === current ? ' selected' : ''));
    button.append(el('span','▤','book'),el('strong',course.name),el('small',course.teacher || '课程目录'));
    button.onclick = () => selectCourse(course); $('courses').append(button);
  }
  $('courseTitle').textContent = current?.name || '暂无课程'; $('teacher').textContent = current?.teacher || '';
  $('tree').replaceChildren(); $('empty').hidden = !current || !!current.chapters.length;
  $('empty').textContent = current?._loading ? '正在读取这门课的章节目录…' : current?._error || (source === 'live' ? '平台返回的目录暂无章节。' : '这门课暂未导入章节。可导入包含章节的课程 JSON。');
  $('chapterSync').disabled = source !== 'live' || !current || !!current._loading;
  $('chapterSync').textContent = current?._loading ? '读取中…' : '同步目录';
  $('autoStart').disabled = source !== 'live' || !current || automationActive;
  if (current) { renderTree(current.chapters, $('tree'), [current.name]); addLink($('tree'), current.url, '在原平台打开课程 ↗'); }
}
function addLink(parent, url, title) {
  if (!url) return;
  try { const u = new URL(url); if (u.protocol !== 'https:' || !(u.hostname === 'zhihuishu.com' || u.hostname.endsWith('.zhihuishu.com'))) return; } catch { return; }
  const a = el('a',title,'lesson-link'); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; parent.append(a);
}
function renderTree(nodes, parent, path) {
  for (const [index, node] of nodes.entries()) {
    const nextPath = [...path,node.name];
    if (node.children?.length) {
      const details = el('details'); details.open = index === 0; details.append(el('summary',node.name)); renderTree(node.children,details,nextPath); parent.append(details);
    } else {
      const button = el('button',node.name,'lesson'); if(node.duration) button.append(el('span',node.duration));
      button.onclick = () => { document.querySelectorAll('.lesson.selected').forEach(x => x.classList.remove('selected')); button.classList.add('selected'); };
      parent.append(button); addLink(parent,node.url,'在原平台学习此节 ↗');
    }
  }
}
$('settingsBtn').onclick = () => $('settings').showModal();
$('demoBtn').onclick = demo;
$('saveSession').onclick = () => busy($('saveSession'), async () => { const result = await api('session',{cookie:$('cookie').value,authorization:$('authorization').value}); $('cookie').value = ''; $('authorization').value = ''; toast(result.message); });
$('saveAi').onclick = () => busy($('saveAi'), async () => { const result = await api('ai-config',{url:$('aiUrl').value,key:$('aiKey').value,model:$('aiModel').value}); $('aiKey').value = ''; $('aiState').textContent = '已配置 · 未验证'; toast(result.message); });
$('clearBtn').onclick = () => busy($('clearBtn'), async () => { const result = await api('clear'); ['cookie','authorization','aiKey'].forEach(id => $(id).value = ''); $('aiState').textContent = '未配置'; toast(result.message); });
$('syncBtn').onclick = () => busy($('syncBtn'), async () => { const result = await api('courses'); setCourses(result.courses,'live'); const warnings = result.warnings || []; $('readWarnings').hidden = !warnings.length; $('readWarnings').textContent = warnings.join('；'); if(result.scope) $('modeText').textContent = result.scope; toast('已读取 ' + result.courses.length + ' 门课程' + (warnings.length ? '，部分课程类型读取失败，见页面提示。' : '')); });
$('chapterSync').onclick = () => loadChapters(current);
$('importFile').onchange = async event => {
  const file = event.target.files[0]; if (!file) return;
  try { if(file.size > 1900000) throw Error('文件请小于 1.9 MB'); const result = await api('import',{data:JSON.parse(await file.text())}); const merged = new Map(source === 'demo' ? [] : courses.map(c => [c.id,c])); for(const course of result.courses) { const old = merged.get(course.id); if(old && !course.chapters.length) course.chapters = old.chapters; merged.set(course.id,course); } setCourses([...merged.values()],'import'); toast('已导入 ' + result.courses.length + ' 门课程'); } catch(e) { toast(e.message); } finally { event.target.value = ''; }
};
const autoLabels = {idle:'未启动', starting:'准备播放', running:'运行中', answering:'AI 处理中', waiting_user:'等待人工处理', paused:'已暂停', stopping:'停止中', stopped:'已停止', error:'运行失败'};
let autoPolling = false;
function showAutomation(state) {
  $('autoBadge').textContent = autoLabels[state.state] || state.state;
  $('autoMessage').textContent = (state.course ? state.course + '：' : '') + state.message;
  const active = ['starting','running','answering','waiting_user','paused','stopping'].includes(state.state);
  automationActive = active;
  $('autoStart').disabled = active || source !== 'live' || !current;
  $('autoPause').disabled = !active || state.state === 'stopping';
  $('autoResume').disabled = !active || state.state === 'stopping';
  $('autoStop').disabled = !active;
  const seconds = Number(state.current_seconds || 0), duration = Number(state.duration_seconds || 0);
  const format = value => Math.floor(value / 60) + ':' + String(value % 60).padStart(2,'0');
  $('autoProgress').textContent = '本次检测到播放结束 ' + (state.completed || 0) + ' 节' + (duration ? ' · 当前播放 ' + format(seconds) + ' / ' + format(duration) : '');
}
async function pollAutomation() {
  if(autoPolling || document.hidden) return;
  autoPolling = true;
  try { showAutomation(await api('automation-status')); }
  catch(error) { $('autoMessage').textContent = error.message; }
  finally { autoPolling = false; }
}
$('autoStart').onclick = () => busy($('autoStart'), async () => {
  if(!current || source !== 'live') throw Error('请先读取并选择真实平台课程');
  showAutomation(await api('automation-start',{course_id:current.id,auto_answer:$('autoAnswer').checked,start_unlearned:$('startUnlearned').checked}));
});
for(const [id, action] of [['autoPause','pause'],['autoResume','resume'],['autoStop','stop']]) {
  $(id).onclick = () => busy($(id), async () => showAutomation(await api('automation-command',{action})));
}
setInterval(pollAutomation,2500);
demo();
pollAutomation();
