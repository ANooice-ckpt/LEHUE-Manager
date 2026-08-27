(() => {
  const baseRenderSubjectRows = window.renderSubjectRows;
  if (typeof baseRenderSubjectRows !== 'function') return;

  async function skipPreparationTest(participantId) {
    const subject = subjectCache.find(row => row.participant_id === participantId);
    if (!subject || subject.status !== 'scheduled' || !subject.preparation_started_at_utc) {
      toast('该被试当前不在准备测试中', 'error');
      return;
    }
    const confirmed = confirm(
      `跳过被试 ${participantId} 的准备测试？\n\n` +
      '系统会直接标记为 Ready，不会伪造 GPS 或 Lighting 测试数据。随后即可“正式开始”。'
    );
    if (!confirmed) return;
    try {
      await api(`/api/v1/web/subjects/${encodeURIComponent(participantId)}/skip-preparation-test`, {
        method: 'POST',
        body: '{}',
      });
      toast(`被试 ${participantId} 已跳过测试并标记 Ready`);
      await renderSubjects();
    } catch (error) {
      toast(`跳过测试失败：${error.message}`, 'error');
    }
  }

  window.renderSubjectRows = function (...args) {
    const result = baseRenderSubjectRows.apply(this, args);
    document.querySelectorAll('#subjectRows tr[data-participant-id]').forEach(row => {
      const participantId = row.dataset.participantId;
      const subject = subjectCache.find(item => item.participant_id === participantId);
      if (!subject || subject.status !== 'scheduled' || !subject.preparation_started_at_utc) return;
      const actions = row.querySelector('.subject-actions');
      if (!actions || actions.querySelector('[data-skip-preparation-test]')) return;

      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'btn small';
      button.dataset.skipPreparationTest = participantId;
      button.textContent = '跳过测试';
      button.addEventListener('click', () => { void skipPreparationTest(participantId); });

      const editButton = actions.querySelector('[data-edit-subject]');
      editButton ? actions.insertBefore(button, editButton) : actions.prepend(button);
    });
    return result;
  };
})();
