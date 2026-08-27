(() => {
  const COMMON_SETTINGS = {
    accuracy: 'high',
    distance: '0',
    interval: '5',
    heartbeat: '0',
    buffer: 'true',
    stop_detection: 'false',
  };

  function buildTraccarUri(platform) {
    const participantId = document.getElementById('credGpsUser')?.value.trim() || '';
    const secret = document.getElementById('credGpsPassword')?.value || '';
    const ownTracksUrl = document.getElementById('credGpsUrl')?.value.trim() || '';
    if (!participantId || !secret || !ownTracksUrl) return '';

    const traccarUrl = ownTracksUrl.endsWith('/owntracks')
      ? ownTracksUrl.slice(0, -'/owntracks'.length) + '/traccar'
      : `${window.location.origin}/api/v1/gps/traccar`;
    const settings = {
      url: traccarUrl,
      id: `${participantId}.${secret}`,
      ...COMMON_SETTINGS,
    };
    if (platform === 'android') {
      settings.wakelock = 'true';
      settings.prefer_platform_providers = 'false';
    }
    return `org.traccar.client://config?${new URLSearchParams(settings).toString()}`;
  }

  async function copyUri(input) {
    const value = input.value;
    if (!value) {
      toast('请先生成 GPS 密码');
      return;
    }
    try {
      await navigator.clipboard.writeText(value);
      toast('Traccar 配置链接已复制');
    } catch {
      window.prompt('请复制 Traccar 配置链接：', value);
    }
  }

  function createPlatformRow(platform, label) {
    const wrapper = document.createElement('div');
    wrapper.style.marginTop = '10px';

    const heading = document.createElement('div');
    heading.style.fontWeight = '600';
    heading.style.marginBottom = '6px';
    heading.textContent = label;
    wrapper.appendChild(heading);

    const row = document.createElement('div');
    row.className = 'row';

    const input = document.createElement('input');
    input.className = 'mono';
    input.readOnly = true;
    input.style.flex = '1';
    input.id = platform === 'android' ? 'traccarAndroidUri' : 'traccarIosUri';
    row.appendChild(input);

    const copy = document.createElement('button');
    copy.type = 'button';
    copy.className = 'btn small';
    copy.textContent = '复制链接';
    copy.addEventListener('click', () => { void copyUri(input); });
    row.appendChild(copy);

    const launch = document.createElement('a');
    launch.className = 'btn small';
    launch.id = platform === 'android' ? 'traccarAndroidLaunch' : 'traccarIosLaunch';
    launch.textContent = '本机打开';
    launch.href = '#';
    launch.addEventListener('click', event => {
      const uri = buildTraccarUri(platform);
      if (!uri) {
        event.preventDefault();
        toast('请先生成 GPS 密码');
        return;
      }
      launch.href = uri;
    });
    row.appendChild(launch);

    wrapper.appendChild(row);
    return wrapper;
  }

  function refreshTraccarLinks() {
    const pairs = [
      ['android', 'traccarAndroidUri', 'traccarAndroidLaunch'],
      ['ios', 'traccarIosUri', 'traccarIosLaunch'],
    ];
    for (const [platform, inputId, launchId] of pairs) {
      const input = document.getElementById(inputId);
      const launch = document.getElementById(launchId);
      if (!input || !launch) continue;
      const uri = buildTraccarUri(platform);
      input.value = uri;
      launch.href = uri || '#';
      launch.setAttribute('aria-disabled', String(!uri));
    }
  }

  function mountTraccarAdminGuide() {
    const dialog = document.getElementById('onboardingDialog');
    if (!dialog || document.getElementById('traccarAdminGuide')) return;

    const ownTracksGuide = dialog.querySelector('.owntracks-guide');
    if (!ownTracksGuide) return;

    const gpsHeading = dialog.querySelector('.credential-sections section:first-child strong');
    if (gpsHeading?.textContent.trim() === 'GPS / OwnTracks') {
      gpsHeading.textContent = 'GPS / OwnTracks / Traccar';
    }

    const guide = document.createElement('div');
    guide.className = 'owntracks-guide';
    guide.id = 'traccarAdminGuide';

    const title = document.createElement('strong');
    title.textContent = 'Traccar 双平台配置';
    guide.appendChild(title);

    const note = document.createElement('p');
    note.textContent = '下面直接给出配置链接。电脑端没有 Traccar Client 时“本机打开”不会生效；可复制链接发送到对应手机打开。两端与 OwnTracks 共用当前被试和 GPS 密码。';
    guide.appendChild(note);

    guide.appendChild(createPlatformRow('ios', 'iOS 配置链接'));
    const iosHint = document.createElement('p');
    iosHint.className = 'muted';
    iosHint.textContent = 'iOS：High、distance 0、离线缓存、Stop detection 关闭；CoreLocation 的实际采样节奏由 iOS 调度，不保证严格 5 s。';
    guide.appendChild(iosHint);

    guide.appendChild(createPlatformRow('android', 'Android 配置链接'));
    const androidHint = document.createElement('p');
    androidHint.className = 'muted';
    androidHint.textContent = 'Android：High、distance 0、请求间隔 5 s、离线缓存、Wake lock、Stop detection 关闭。';
    guide.appendChild(androidHint);

    ownTracksGuide.insertAdjacentElement('afterend', guide);
    refreshTraccarLinks();
  }

  mountTraccarAdminGuide();

  const originalShowOnboarding = window.showOnboarding;
  if (typeof originalShowOnboarding === 'function') {
    window.showOnboarding = function (...args) {
      const result = originalShowOnboarding.apply(this, args);
      mountTraccarAdminGuide();
      refreshTraccarLinks();
      return result;
    };
  }
})();
