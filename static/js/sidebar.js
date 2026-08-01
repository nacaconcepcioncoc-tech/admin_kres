(function(){
  const mobileQuery=window.matchMedia('(max-width: 768px)');

  function applySidebarState(collapsed){
    const sidebar=document.getElementById('sidebar');
    const main=document.getElementById('mainContent');
    const toggle=document.getElementById('sidebarToggle');
    if(!sidebar||!main||!toggle)return;
    const effectiveCollapsed=mobileQuery.matches||collapsed;
    sidebar.classList.toggle('collapsed',effectiveCollapsed);
    main.classList.toggle('sidebar-expanded',effectiveCollapsed);
    main.classList.remove('expanded');
    toggle.setAttribute('aria-expanded',String(!effectiveCollapsed));
  }
  function initSidebar(){
    const toggle=document.getElementById('sidebarToggle');
    if(!toggle)return;
    const restore=function(){
      applySidebarState(localStorage.getItem('sidebarCollapsed')==='true');
    };
    restore();
    toggle.addEventListener('click',function(){
      const sidebar=document.getElementById('sidebar');
      const collapsed=!sidebar.classList.contains('collapsed');
      localStorage.setItem('sidebarCollapsed',String(collapsed));
      applySidebarState(collapsed);
    });
    if(mobileQuery.addEventListener)mobileQuery.addEventListener('change',restore);
    else mobileQuery.addListener(restore);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initSidebar);else initSidebar();
})();
