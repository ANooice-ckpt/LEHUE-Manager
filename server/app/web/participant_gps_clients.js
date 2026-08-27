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

  function addConfigLink(container, href, label, recommended = false) {
    if (!href) return;
    const launch = document.createElement('a');
    launch.className = recommended ? 'submit recommended' : 'submit';
    launch.href = href;
    launch.textContent = label;
    container.appendChild(launch);
  }

  function buildTraccarBlock(config) {
    const block = document.createElement('div');
    block.className = 'special';
    block.dataset.traccarConfig = '1';

    const title = document.createElement('strong');
    title.textContent = 'Traccar 双平台配置';
    block.appendChild(title);

    const note = document.createElement('div');
    note.className = 'portal-note';
    note.textContent = '以下是直接配置链接，请在已安装 Traccar Client 的对应手机上打开；与 OwnTracks 共用同一个被试身份和 GPS 密码。';
    block.appendChild(note);

    const downloads = document.createElement('div');
    downloads.className = 'config-downloads';
    const androidUri = config.platforms?.android?.uri || config.uri || '';
    const iosUri = config.platforms?.ios?.uri || '';
    addConfigLink(downloads, iosUri, 'iOS · Traccar 配置链接');
    addConfigLink(downloads, androidUri, 'Android · Traccar 配置链接', true);
    block.appendChild(downloads);

    const androidHint = document.createElement('div');
    androidHint.className = 'portal-note';
    androidHint.textContent = 'Android：High、distance 0、请求间隔 5 s、离线缓存、Wake lock，Stop detection 关闭。';
    block.appendChild(androidHint);

    const iosHint = document.createElement('div');
    iosHint.className = 'portal-note';
    iosHint.textContent = 'iOS：High、distance 0、离线缓存，Stop detection 关闭；CoreLocation 的实际采样节奏由 iOS 调度，不保证严格 5 s。';
    block.appendChild(iosHint);

    const hint = document.createElement('div');
    hint.className = 'portal-note';
    hint.textContent = '配置后请允许始终/后台定位。Android 还需将系统电池策略设为不限制；Portal 的 GPS 状态和轨迹与 OwnTracks 共用。';
    block.appendChild(hint);
    return block;
  }

  async function mountTraccarConfig() {
    const host = document.getElementById('studyHelp');
    if (!host || host.querySelector('[data-traccar-config]')) return;
    const config = await loadTraccarConfig();
    const androidUri = config.platforms?.android?.uri || config.uri || '';
    const iosUri = config.platforms?.ios?.uri || '';
    if (!config.available || (!androidUri && !iosUri) || host.querySelector('[data-traccar-config]')) return;

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
