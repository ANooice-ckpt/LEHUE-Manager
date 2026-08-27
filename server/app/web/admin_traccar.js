(() => {
  const TRACCAR_SETTINGS = {
    accuracy: 'high',
    distance: '0',
    interval: '5',
    heartbeat: '0',
    buffer: 'true',
    wakelock: 'true',
    stop_detection: 'false',
    prefer_platform_providers: 'false',
  };

  function buildTraccarUri() {
    const participantId = document.getElementById('credGpsUser')?.value.trim() || '';
    const secret = document.getElementById('credGpsPassword')?.value || '';
    if (!participantId || !secret) return '';

    const params = new URLSearchParams({
      url: `${window.location.origin}/api/v1/gps/traccar`,
      id: `${participantId}.${secret}`,
      ...TRACCAR_SETTINGS,
    });
    return `org.traccar.client://config?${params.toString()}`;
  }

  function refreshTraccarLink() {
    const launch = document.getElementById('traccarAndroidLaunch');
    if (!launch) return;
    const uri = buildTraccarUri();
    launch.href = uri || '#';
    launch.setAttribute('aria-disabled', String(!uri));
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
    title.textContent = 'Traccar Android 配置';
    guide.appendChild(title);

    const note = document.createElement('p');
    note.textContent = '与 OwnTracks 共用当前被试和 GPS 密码；一键写入 High、5 s、离线缓存、Wake lock，并关闭 Stop detection。';
    guide.appendChild(note);

    const actions = document.createElement('div');
    actions.className = 'form-actions';
    const launch = document.createElement('a');
    launch.className = 'btn';
    launch.id = 'traccarAndroidLaunch';
    launch.textContent = 'Android · Traccar 一键配置';
    launch.href = '#';
    launch.addEventListener('click', event => {
      const uri = buildTraccarUri();
      if (!uri) {
        event.preventDefault();
        toast('请先生成 GPS 密码');
        return;
      }
      launch.href = uri;
    });
    actions.appendChild(launch);
    guide.appendChild(actions);

    ownTracksGuide.insertAdjacentElement('afterend', guide);
    refreshTraccarLink();
  }

  mountTraccarAdminGuide();

  const originalShowOnboarding = window.showOnboarding;
  if (typeof originalShowOnboarding === 'function') {
    window.showOnboarding = function (...args) {
      const result = originalShowOnboarding.apply(this, args);
      mountTraccarAdminGuide();
      refreshTraccarLink();
      return result;
    };
  }
})();
