// ==UserScript==
// @name         知到：手动导出已加载的课程目录
// @namespace    local-study-companion
// @version      0.1.0
// @description  仅在点击按钮时读取页面已加载的 Vue 数据并下载 JSON；不读取凭据、不发送请求、不操作播放或答案。
// @match        https://onlineweb.zhihuishu.com/*
// @match        https://onlinestuh5.zhihuishu.com/*
// @match        https://studyvideoh5.zhihuishu.com/*
// @grant        none
// @run-at       document-idle
// ==/UserScript==
(() => {
  'use strict';
  if (document.getElementById('local-study-export')) return;
  const button = document.createElement('button');
  button.id = 'local-study-export'; button.textContent = '导出已加载课程';
  Object.assign(button.style,{position:'fixed',right:'20px',bottom:'22px',zIndex:'2147483647',background:'#275d48',color:'white',border:'none',borderRadius:'8px',padding:'12px 18px',cursor:'pointer'});
  document.body.append(button);
  function chapterNodes(value, depth = 0) {
    if (depth > 12) return [];
    if (!Array.isArray(value)) {
      if (!value || typeof value !== 'object') return [];
      value = value.videoChapterDtos || value.chapterList || value.chapters || [];
    }
    return value.filter(x => x && typeof x === 'object').map((x, i) => {
      const children = x.children || x.videoLessons || x.videoSmallLessons || x.lessonList || x.chapterList || [];
      const node = {name: String(x.name || x.chapterName || x.lessonName || x.videoName || `目录项 ${i+1}`)};
      const nested = chapterNodes(children,depth+1); if(nested.length) node.children = nested;
      return node;
    });
  }
  button.onclick = () => {
    const seen = new Set(), list = new Map();
    function visit(vm) {
      if (!vm || seen.has(vm)) return; seen.add(vm);
      const data = vm.$data || {};
      // Only select known fields. Never serialize a whole component or browser storage.
      if (Array.isArray(data.sharingData)) for (const c of data.sharingData) {
        const id = c.courseId ?? c.id; if(id == null) continue;
        const course = {id:String(id),name:String(c.courseName || c.name || '未命名课程'),chapters:[]};
        if(c.teacherName) course.teacher = String(c.teacherName);
        if(c.recruitAndCourseId) course.url = 'https://studyvideoh5.zhihuishu.com/stuStudy?recruitAndCourseId=' + encodeURIComponent(c.recruitAndCourseId);
        list.set(course.id,course);
      }
      if (data.videoData && data.courseId) {
        const chapters = chapterNodes(data.videoData);
        if (chapters.length) list.set(String(data.courseId), {id:String(data.courseId),name:String(data.videoData.courseName || data.courseName || document.title),chapters,url:location.href});
      }
      for (const child of vm.$children || []) visit(child);
    }
    for (const element of document.querySelectorAll('*')) if (element.__vue__) visit(element.__vue__);
    if (!list.size) { alert('未识别已加载的课程数据。请在课程列表或视频目录加载完成后重试；当前页面类型可能尚不支持。'); return; }
    const url = URL.createObjectURL(new Blob([JSON.stringify({courses:[...list.values()]},null,2)],{type:'application/json'}));
    const a = document.createElement('a'); a.href = url; a.download = '知到课程目录.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
    alert('已导出 ' + list.size + ' 门课程。只含当前页面已加载的数据；未加载的分页、课程类型或章节不会出现在文件中。');
  };
})();
