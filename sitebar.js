// Shared header extras for every page: Local Guide badge, QR share button,
// the slim bar that stays at the top after scrolling, and the QR pop-up.
// Each page loads this with <script src="/sitebar.js" defer></script>.
(function () {
    var GOOGLE = 'https://maps.app.goo.gl/w3JyVs56oL8oc73r7';
    var header = document.querySelector('.site-header');
    var nav = document.querySelector('.site-nav');
    if (!header || !nav) return;
    document.body.classList.add('has-stickybar');

    function openQR() { document.getElementById('qr-box').showModal(); }

    // 1. Big badge and QR button under the title (skipped if the page already has them)
    if (!header.querySelector('.lg-badge')) {
        header.insertAdjacentHTML('beforeend',
            '<a class="lg-badge" href="' + GOOGLE + '" target="_blank" rel="noopener" aria-label="Google Local Guide Level 7, see my Google reviews">' +
            '<img src="/local-guide-level-7.png" width="520" height="328" alt="Google Local Guide Level 7 badge"></a>' +
            '<button type="button" class="qr-open">Share this site · QR code</button>');
    }
    header.querySelectorAll('.qr-open').forEach(function (b) { b.removeAttribute('onclick'); b.addEventListener('click', openQR); });

    // 2. QR pop-up
    if (!document.getElementById('qr-box')) {
        document.body.insertAdjacentHTML('beforeend',
            '<dialog id="qr-box" class="qr-box" aria-label="QR code for Toronto Eats and Reviews">' +
            '<p class="qr-title">Toronto Eats and Reviews<span class="dot">.</span></p>' +
            '<img src="/site-qr.png" width="396" height="396" alt="QR code that opens torontoeatsandreviews.com">' +
            '<p class="qr-url">torontoeatsandreviews.com</p>' +
            '<div class="qr-actions"><a href="/site-qr.png" download="toronto-eats-and-reviews-qr.png">Save QR</a>' +
            '<button type="button" class="qr-close">Close</button></div></dialog>');
    }
    var box = document.getElementById('qr-box');
    box.addEventListener('click', function (e) { if (e.target === box) box.close(); });
    box.querySelector('.qr-close').addEventListener('click', function () { box.close(); });

    // 3. Slim bar that slides in once the big header scrolls away
    document.body.insertAdjacentHTML('afterbegin',
        '<div class="stickybar" id="stickybar" aria-hidden="true">' +
        '<a href="/" class="sb-title"><img src="/icon-192.png" width="36" height="36" alt=""><span class="sb-name">Toronto Eats and Reviews<span class="dot">.</span></span></a>' +
        '<a class="sb-badge" href="' + GOOGLE + '" target="_blank" rel="noopener" aria-label="Google Local Guide Level 7"><img src="/local-guide-star.png" width="30" height="30" alt=""><span>Local Guide <b>Level 7</b></span></a>' +
        '<button type="button" class="sb-qr" aria-label="Show QR code">QR</button>' +
        '<button type="button" class="sb-menu" aria-label="Open menu" aria-expanded="false" aria-controls="sb-links"><span></span><span></span><span></span></button>' +
        '<nav class="sb-links" id="sb-links" aria-label="Menu" hidden></nav></div>');
    var bar = document.getElementById('stickybar');
    var btn = bar.querySelector('.sb-menu');
    var links = document.getElementById('sb-links');
    links.innerHTML = nav.innerHTML;
    bar.querySelector('.sb-qr').addEventListener('click', openQR);
    function closeMenu() { links.hidden = true; btn.setAttribute('aria-expanded', 'false'); }
    btn.addEventListener('click', function () {
        links.hidden = !links.hidden;
        btn.setAttribute('aria-expanded', String(!links.hidden));
    });
    links.addEventListener('click', function (e) { if (e.target.closest('a')) closeMenu(); });
    new IntersectionObserver(function (entries) {
        var show = !entries[0].isIntersecting;
        bar.classList.toggle('show', show);
        bar.setAttribute('aria-hidden', String(!show));
        if (!show) closeMenu();
    }).observe(header);
})();
