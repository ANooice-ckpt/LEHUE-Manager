(() => {
  const pathParts = window.location.pathname.split('/').filter(Boolean);
  if (pathParts[0] !== 'p' || !pathParts[1]) return;
  const portalToken = pathParts[1];
  let configPromise = null;

  function loadTraccarConfig() {
    if (!configPromise) {
      configPromise = fetch(`/api/v1/portal/${encodeURIComponent(portalToken)}/traccar/config`, {
        credentials: 'same-origin',
        cache: 'no-store',
      })
        .then((response) => response.ok ? response.json() : { available: false })
        .catch(() => ({ available: false }));
    }
    return configPromise;
  }

  function buildTraccarBlock(config) {
    const block = document.createElement('div');
    block.className = 'special';
    block.dataset.traccarConfig = '1';

    const title = document.createElement('strong');
    title.textContent = 'Traccar 配置';
    block.appendChild(title);

    const note = document.createElement('div');
    note.className = 'portal-note';
    note.textContent = 'Android 可用。与 OwnTracks 使用同一个被试身份和 GPS 密码；一键写入 Highest、5 s、离线缓存、Wake lock，并关闭 Stop detection。';
    block.appendChild(note);

    const downloads = document.createElement('div');
    downloads.className = 'config-downloads';
    const launch = document.createElement('a');
    launch.className = 'submit recommended';
    launch.href = config.uri;
    launch.textContent = 'Android · Traccar 一键配置';
    downloads.appendChild(launch);
    block.appendChild(downloads);

    const hint = document.createElement('div');
    hint.className = 'portal-note';
    hint.textContent = '请先安装 Traccar Client。配置后允许后台定位，并将系统电池策略设为不限制；Portal 的 GPS 状态和轨迹与 OwnTracks 共用。';
    block.appendChild(hint);
    return block;
  }

  async function mountTraccarConfig() {
    const host = document.getElementById('studyHelp');
    if (!host || host.querySelector('[data-traccar-config]')) return;
    const config = await loadTraccarConfig();
    if (!config.available || !config.uri || host.querySelector('[data-traccar-config]')) return;

    const block = buildTraccarBlock(config);
    const ownTracks = Array.from(host.children).find(
      (node) => node.classList?.contains('special') && node.textContent.includes('OwnTracks 配置下载'),
    );
    if (ownTracks) ownTracks.insertAdjacentElement('afterend', block);
    else host.prepend(block);
  }

  const observer = new MutationObserver(() => { void mountTraccarConfig(); });
  observer.observe(document.body, { childList: true, subtree: true });
  void mountTraccarConfig();
})();
