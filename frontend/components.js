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

/**
 * Attaches a live-search autocomplete dropdown to a text input.
 *
 * @param {string}   inputId      - ID of the <input> element
 * @param {Function|string} tourGetter    - () => tour string, or a static string e.g. 'ATP'
 * @param {Function} onSelect     - called with (playerName, tour) when the user picks a result
 * @param {Function|string} [surfaceGetter] - optional () => surface string e.g. 'Clay'.
 *                                            When provided, results are filtered to players
 *                                            who have stats on that surface. Pass null to
 *                                            search across all surfaces (default).
 */
window.setupPlayerAutocomplete = function(inputId, tourGetter, onSelect, surfaceGetter) {
    const input = document.getElementById(inputId);
    if (!input) return;

    let wrap = input.parentElement;
    if (getComputedStyle(wrap).position === 'static') {
        wrap.style.position = 'relative';
    }

    const drop = document.createElement('ul');
    drop.style.cssText = [
        'position:absolute', 'top:100%', 'left:0', 'right:0', 'z-index:200',
        'background:#fff', 'border:1px solid #E9ECEF', 'max-height:220px',
        'overflow-y:auto', 'box-shadow:0 4px 12px rgba(0,0,0,.1)',
        'display:none', 'list-style:none', 'padding:0', 'margin:0', 'text-align:left',
    ].join(';');
    wrap.appendChild(drop);

    function buildUrl(q) {
        const tour    = typeof tourGetter    === 'function' ? tourGetter()    : (tourGetter    || 'ATP');
        const surface = typeof surfaceGetter === 'function' ? surfaceGetter() : (surfaceGetter || '');
        let url = `/api/players/search?q=${encodeURIComponent(q)}&tour=${tour}`;
        if (surface) url += `&surface=${encodeURIComponent(surface)}`;
        return { url, tour };
    }

    let timer;
    input.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
            const q = input.value.trim();
            if (q.length < 2) { drop.style.display = 'none'; return; }
            const { url, tour } = buildUrl(q);
            try {
                const res  = await fetch(url);
                const list = await res.json();
                drop.innerHTML = '';
                if (!list.length) {
                    // Show a friendly "no results" hint so the user knows the
                    // surface filter is active and affecting results
                    const surface = typeof surfaceGetter === 'function' ? surfaceGetter() : '';
                    const li = document.createElement('li');
                    li.style.cssText = 'padding:10px 16px;font-family:Lexend,sans-serif;font-size:11px;color:#757682;font-style:italic;';
                    li.textContent = surface
                        ? `No ${surface} stats found for "${q}"`
                        : `No players found for "${q}"`;
                    drop.appendChild(li);
                    drop.style.display = 'block';
                    return;
                }
                list.forEach(name => {
                    const li = document.createElement('li');
                    li.style.cssText = 'padding:8px 16px;cursor:pointer;font-family:Lexend,sans-serif;font-size:12px;font-weight:700;text-transform:uppercase;border-bottom:1px solid #f3f4f5;color:#00113a;';
                    li.textContent = name;
                    li.onmouseenter = () => li.style.background = '#f8f9fa';
                    li.onmouseleave = () => li.style.background = '';
                    li.addEventListener('mousedown', e => {
                        e.preventDefault();
                        input.value = name;
                        drop.style.display = 'none';
                        if (onSelect) onSelect(name, tour);
                    });
                    drop.appendChild(li);
                });
                drop.style.display = 'block';
            } catch(e) { console.error(e); }
        }, 200);
    });

    input.addEventListener('blur', () => setTimeout(() => { drop.style.display = 'none'; }, 150));

    // Expose a method so the simulator page can force-close the dropdown and
    // re-trigger a search whenever the tour/surface selectors change.
    input._autocompleteRefresh = () => {
        drop.style.display = 'none';
        if (input.value.trim().length >= 2) input.dispatchEvent(new Event('input'));
    };
};