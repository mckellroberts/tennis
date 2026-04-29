class AppHeader extends HTMLElement {
    connectedCallback() {
        const p = window.location.pathname;
        const activeClass = "nav-link nav-link-active";
        const inactiveClass = "nav-link nav-link-inactive";
        
        this.innerHTML = `
<header class="bg-white dark:bg-slate-900 border-b border-[#E9ECEF] dark:border-slate-800 sticky top-0 z-50">
<nav class="flex justify-between items-center px-6 py-3 w-full max-w-full mx-auto">
<div class="nav-brand">COURT ANALYTICS</div>
<div class="hidden md:flex items-center gap-8">
<a class="${p.includes('index.html') || p === '/' || p.endsWith('frontend/') ? activeClass : inactiveClass}" href="index.html">Home</a>
<a class="${p.includes('playerSearch.html') || p.includes('player.html') ? activeClass : inactiveClass}" href="playerSearch.html">Players</a>
<a class="${p.includes('simulator.html') ? activeClass : inactiveClass}" href="simulator.html">Simulation</a>
</div>
<div class="flex items-center gap-4">
<span class="material-symbols-outlined text-[#002366]">account_circle</span>
</div>
</nav>
</header>`;
    }
}
customElements.define('app-header', AppHeader);

class AppBottomNav extends HTMLElement {
    connectedCallback() {
        const p = window.location.pathname;
        const activeClass = "bottom-nav-link bottom-nav-link-active";
        const inactiveClass = "bottom-nav-link bottom-nav-link-inactive";

        this.innerHTML = `
<nav class="md:hidden fixed bottom-0 w-full z-50 flex justify-around items-center bg-white dark:bg-slate-900 px-4 pb-safe border-t border-[#E9ECEF] dark:border-slate-800">
<a class="${p.includes('index.html') || p === '/' || p.endsWith('frontend/') ? activeClass : inactiveClass}" href="index.html"><span class="material-symbols-outlined">home</span><span>Home</span></a>
<a class="${p.includes('playerSearch.html') || p.includes('player.html') ? activeClass : inactiveClass}" href="playerSearch.html"><span class="material-symbols-outlined">person</span><span>Players</span></a>
<a class="bottom-nav-link bottom-nav-link-inactive" href="#"><span class="material-symbols-outlined">grid_view</span><span>Brackets</span></a>
<a class="${p.includes('simulator.html') ? activeClass : inactiveClass}" href="simulator.html"><span class="material-symbols-outlined">analytics</span><span>Simulate</span></a>
</nav>`;
    }
}
customElements.define('app-bottom-nav', AppBottomNav);
