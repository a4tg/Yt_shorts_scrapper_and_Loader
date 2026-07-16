const REACTIONS = ['👍', '❤️', '🔥', '🎉', '👀', '✅'];
const LAYOUT_KEY = 'aapChatAnywhereLayoutV1';

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function initials(value = '') {
  return value.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join('').toUpperCase() || 'AAP';
}

function messageTime(value) {
  if (!value) return '';
  const date = new Date(value); const today = new Date();
  return date.toDateString() === today.toDateString()
    ? date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
    : date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
}

function loadLayout() {
  try { return JSON.parse(localStorage.getItem(LAYOUT_KEY)) || {}; } catch (_) { return {}; }
}

export function initChatAnywhere({ bus, router, bridge }) {
  const state = {
    context: bridge?.getContext?.() || null,
    conversations: [], messages: [], library: [], members: [],
    activeConversationId: null, replyTo: null, mentionedIds: new Set(),
    source: null, refreshTimer: null, projectId: null, opened: false,
  };
  const layout = { mode: 'floating', left: null, top: null, width: 430, height: 650, ...loadLayout() };

  const launcher = element('button', 'chat-anywhere-launcher');
  launcher.type = 'button'; launcher.setAttribute('aria-label', 'Открыть чат');
  launcher.innerHTML = '<span aria-hidden="true">◌</span><b>Чат</b><i class="hidden">0</i>';

  const shell = element('section', 'chat-anywhere hidden');
  shell.setAttribute('role', 'dialog'); shell.setAttribute('aria-label', 'Чат проекта');
  shell.innerHTML = `
    <header class="chat-anywhere-head">
      <button class="chat-anywhere-conversations-toggle" type="button" aria-label="Показать диалоги">☰</button>
      <span class="chat-anywhere-avatar">AAP</span>
      <div><strong>Чат проекта</strong><small>Выберите проект</small></div>
      <button data-chat-control="full" type="button" title="Открыть inbox" aria-label="Открыть полную страницу сообщений">↗</button>
      <button data-chat-control="dock" type="button" title="Закрепить сбоку" aria-label="Закрепить окно сбоку">◧</button>
      <button data-chat-control="minimize" type="button" title="Свернуть" aria-label="Свернуть окно">—</button>
      <button data-chat-control="close" type="button" title="Закрыть" aria-label="Закрыть окно">×</button>
    </header>
    <div class="chat-anywhere-body">
      <aside class="chat-anywhere-sidebar">
        <label><span class="sr-only">Поиск диалогов</span><input type="search" placeholder="Найти диалог"></label>
        <div class="chat-anywhere-conversations"></div>
      </aside>
      <main class="chat-anywhere-main">
        <header class="chat-anywhere-thread-head">
          <div><strong>Сообщения</strong><small>Контекст остаётся рядом с работой</small></div>
          <span class="chat-anywhere-live" title="Realtime соединение"><i></i> live</span>
          <button data-chat-action="pins" type="button" aria-label="Закреплённые сообщения">⌖</button>
        </header>
        <div class="chat-anywhere-context hidden"></div>
        <div class="chat-anywhere-messages" aria-live="polite"></div>
        <div class="chat-anywhere-pins hidden"></div>
        <div class="chat-anywhere-reply hidden"><div><small>Ответ</small><strong></strong></div><button type="button" aria-label="Отменить ответ">×</button></div>
        <div class="chat-anywhere-mentions hidden"></div>
        <form class="chat-anywhere-composer">
          <textarea rows="2" maxlength="10000" placeholder="Напишите сообщение…" aria-label="Текст сообщения"></textarea>
          <footer>
            <label class="chat-anywhere-attachment"><span>＋ Файл</span><select aria-label="Прикрепить файл"><option value="">Без вложения</option></select></label>
            <button data-chat-action="mention" type="button" aria-label="Упомянуть участника">@</button>
            <span></span><button class="primary" type="submit">Отправить ↗</button>
          </footer>
        </form>
      </main>
    </div>`;
  document.body.append(launcher, shell);

  const $ = (selector) => shell.querySelector(selector);
  const conversationList = $('.chat-anywhere-conversations');
  const messageList = $('.chat-anywhere-messages');
  const composer = $('.chat-anywhere-composer');
  const textarea = composer.querySelector('textarea');
  const attachmentSelect = composer.querySelector('select');
  const mentionPanel = $('.chat-anywhere-mentions');
  const pinsPanel = $('.chat-anywhere-pins');

  function activeConversation() {
    return state.conversations.find((item) => item.id === state.activeConversationId) || null;
  }

  function draftKey() { return `aapChatDraft:${state.projectId || 'none'}:${state.activeConversationId || 'none'}`; }

  function saveDraft() {
    if (!state.activeConversationId) return;
    if (textarea.value) localStorage.setItem(draftKey(), textarea.value);
    else localStorage.removeItem(draftKey());
  }

  function persistLayout() {
    if (layout.mode === 'floating') {
      const bounds = shell.getBoundingClientRect();
      Object.assign(layout, { left: Math.round(bounds.left), top: Math.round(bounds.top), width: Math.round(bounds.width), height: Math.round(bounds.height) });
    }
    localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
  }

  function applyLayout() {
    shell.dataset.mode = layout.mode;
    shell.style.removeProperty('left'); shell.style.removeProperty('top');
    shell.style.removeProperty('width'); shell.style.removeProperty('height');
    if (layout.mode === 'floating') {
      shell.style.width = `${Math.max(360, layout.width)}px`; shell.style.height = `${Math.max(460, layout.height)}px`;
      if (layout.left !== null) shell.style.left = `${Math.max(8, Math.min(layout.left, innerWidth - 360))}px`;
      if (layout.top !== null) shell.style.top = `${Math.max(8, Math.min(layout.top, innerHeight - 120))}px`;
    }
  }

  function setMode(mode) {
    if (layout.mode === 'floating') persistLayout();
    layout.mode = mode; applyLayout(); persistLayout();
  }

  function updateUnread() {
    const count = state.conversations.reduce((sum, item) => sum + Number(item.unread_count || 0), 0);
    const badge = launcher.querySelector('i'); badge.textContent = count > 99 ? '99+' : String(count);
    badge.classList.toggle('hidden', count === 0);
  }

  function openWindow(conversationId = null, context = null) {
    state.opened = true; shell.classList.remove('hidden'); launcher.classList.add('hidden');
    if (layout.mode === 'closed') setMode('floating'); else applyLayout();
    if (context) {
      const chip = $('.chat-anywhere-context'); chip.textContent = context.title ? `Контекст: ${context.title}` : 'Контекст текущей работы'; chip.classList.remove('hidden');
    }
    if (conversationId) openConversation(conversationId).catch(showError);
    else if (state.activeConversationId) openConversation(state.activeConversationId).catch(showError);
  }

  function closeWindow() {
    saveDraft(); state.opened = false; shell.classList.add('hidden'); launcher.classList.remove('hidden');
  }

  function showError(error) { bridge?.notify?.(error?.message || 'Не удалось обновить чат.', 'error'); }

  function renderConversations() {
    conversationList.replaceChildren();
    const query = $('.chat-anywhere-sidebar input').value.trim().toLocaleLowerCase('ru-RU');
    for (const conversation of state.conversations) {
      if (query && !`${conversation.name} ${conversation.last_message?.body || ''}`.toLocaleLowerCase('ru-RU').includes(query)) continue;
      const button = element('button', `chat-anywhere-conversation${conversation.id === state.activeConversationId ? ' active' : ''}`);
      button.type = 'button'; button.dataset.conversationId = conversation.id;
      const avatar = element('span', `chat-anywhere-conversation-avatar ${conversation.kind}`, conversation.kind === 'context' ? '▤' : initials(conversation.name));
      const copy = element('span'); copy.append(element('strong', '', conversation.name), element('small', '', conversation.last_message?.body || conversation.last_message?.attachment_name || 'Сообщений пока нет'));
      const meta = element('span', 'chat-anywhere-conversation-meta', messageTime(conversation.updated_at));
      if (conversation.unread_count) meta.append(element('b', '', conversation.unread_count));
      button.append(avatar, copy, meta); conversationList.append(button);
    }
    if (!conversationList.children.length) conversationList.append(element('p', 'chat-anywhere-empty', 'Диалоги не найдены'));
    updateUnread();
  }

  function renderAttachment(message) {
    if (!message.attachment && !message.attachment_name) return null;
    if (!message.attachment) return element('div', 'chat-anywhere-file missing', `${message.attachment_name} · файл удалён`);
    const link = element('a', 'chat-anywhere-file'); link.href = message.attachment.download_url;
    link.append(element('span', '', (message.attachment.name.split('.').pop() || 'file').slice(0, 4).toUpperCase()), element('strong', '', message.attachment.name));
    return link;
  }

  function renderMessages({ preserveScroll = false } = {}) {
    const previousBottom = messageList.scrollHeight - messageList.scrollTop;
    messageList.replaceChildren();
    if (!state.messages.length) messageList.append(element('p', 'chat-anywhere-empty', 'Начните обсуждение — контекст останется внутри проекта.'));
    for (const message of state.messages) {
      const row = element('article', `chat-anywhere-message${message.is_own ? ' own' : ''}`); row.dataset.messageId = message.id;
      row.append(element('span', 'chat-anywhere-message-avatar', initials(message.author.name)));
      const bubble = element('div', 'chat-anywhere-bubble');
      const head = element('header'); head.append(element('strong', '', message.author.name), element('time', '', messageTime(message.created_at))); bubble.append(head);
      if (message.is_pinned) bubble.append(element('small', 'chat-anywhere-pinned-label', `⌖ Закрепил ${message.pinned_by?.name || 'участник'}`));
      if (message.reply_to) bubble.append(element('blockquote', '', `${message.reply_to.author_name}: ${message.reply_to.deleted ? 'Сообщение удалено' : message.reply_to.body || 'Вложение'}`));
      if (message.deleted_at) bubble.append(element('p', 'deleted', 'Сообщение удалено'));
      else if (message.body) bubble.append(element('p', '', message.body));
      const file = message.deleted_at ? null : renderAttachment(message); if (file) bubble.append(file);
      if (message.reactions?.length) {
        const reactions = element('div', 'chat-anywhere-reactions');
        for (const reaction of message.reactions) {
          const button = element('button', reaction.reacted_by_me ? 'mine' : '', `${reaction.emoji} ${reaction.count}`);
          button.type = 'button'; button.dataset.action = 'reaction'; button.dataset.emoji = reaction.emoji; reactions.append(button);
        }
        bubble.append(reactions);
      }
      const actions = element('div', 'chat-anywhere-message-actions');
      if (!message.deleted_at) {
        for (const [action, label] of [['reply', 'Ответить'], ['react-menu', 'Реакция'], ['pin', message.is_pinned ? 'Открепить' : 'Закрепить']]) {
          const button = element('button', '', label); button.type = 'button'; button.dataset.action = action; actions.append(button);
        }
        if (message.is_own) {
          const edit = element('button', '', 'Изменить'); edit.type = 'button'; edit.dataset.action = 'edit';
          const remove = element('button', '', 'Удалить'); remove.type = 'button'; remove.dataset.action = 'delete'; actions.append(edit, remove);
        }
      }
      bubble.append(actions); row.append(bubble); messageList.append(row);
    }
    if (preserveScroll) messageList.scrollTop = Math.max(0, messageList.scrollHeight - previousBottom);
    else requestAnimationFrame(() => { messageList.scrollTop = messageList.scrollHeight; });
  }

  function populateAttachments() {
    attachmentSelect.replaceChildren(new Option('Без вложения', ''));
    for (const item of state.library) attachmentSelect.append(new Option(item.name, item.id));
  }

  function renderMentions() {
    mentionPanel.replaceChildren();
    for (const member of state.members.filter((item) => item.user_id !== state.context?.user?.id)) {
      const name = member.display_name || member.email; const button = element('button', '', `@${name}`);
      button.type = 'button'; button.dataset.userId = member.user_id; button.dataset.userName = name; mentionPanel.append(button);
    }
    if (!mentionPanel.children.length) mentionPanel.append(element('p', '', 'Других участников пока нет'));
  }

  async function refreshConversations() {
    if (!state.projectId) return;
    state.conversations = await bridge.api(`/api/projects/${state.projectId}/conversations`);
    if (!state.conversations.some((item) => item.id === state.activeConversationId)) {
      state.activeConversationId = localStorage.getItem(`aapChatConversation:${state.projectId}`) || state.conversations[0]?.id || null;
    }
    renderConversations();
  }

  async function refreshMessages({ preserveScroll = false } = {}) {
    if (!state.activeConversationId) return;
    const result = await bridge.api(`/api/conversations/${state.activeConversationId}/messages`);
    state.messages = result.messages; renderMessages({ preserveScroll });
  }

  async function openConversation(conversationId) {
    if (!state.conversations.some((item) => item.id === conversationId)) await refreshConversations();
    if (!state.conversations.some((item) => item.id === conversationId)) return;
    saveDraft(); state.activeConversationId = conversationId; state.replyTo = null; state.mentionedIds.clear();
    localStorage.setItem(`aapChatConversation:${state.projectId}`, conversationId);
    textarea.value = localStorage.getItem(draftKey()) || '';
    const conversation = activeConversation();
    $('.chat-anywhere-head strong').textContent = conversation.name; $('.chat-anywhere-head small').textContent = conversation.content_title || 'Чат проекта';
    $('.chat-anywhere-avatar').textContent = conversation.kind === 'context' ? '▤' : initials(conversation.name);
    $('.chat-anywhere-thread-head strong').textContent = conversation.name;
    $('.chat-anywhere-thread-head small').textContent = conversation.content_title || `${conversation.participants.length} участников`;
    renderConversations(); await refreshMessages();
    await bridge.api(`/api/conversations/${conversationId}/read`, { method: 'POST' });
    conversation.unread_count = 0; updateUnread();
  }

  async function loadProject(context) {
    state.context = context; const projectId = context?.project?.id;
    if (!projectId || projectId === state.projectId) return;
    state.projectId = projectId; state.activeConversationId = null; state.messages = [];
    closeRealtime();
    [state.conversations, state.library, state.members] = await Promise.all([
      bridge.api(`/api/projects/${projectId}/conversations`),
      bridge.api(`/api/projects/${projectId}/library`),
      bridge.api(`/api/workspaces/${context.workspace.id}/members`),
    ]);
    state.activeConversationId = localStorage.getItem(`aapChatConversation:${projectId}`) || state.conversations[0]?.id || null;
    populateAttachments(); renderMentions(); renderConversations(); connectRealtime();
    if (state.opened && state.activeConversationId) await openConversation(state.activeConversationId);
  }

  function closeRealtime() {
    state.source?.close(); state.source = null; $('.chat-anywhere-live').classList.remove('connected');
  }

  function scheduleRealtimeRefresh() {
    clearTimeout(state.refreshTimer);
    state.refreshTimer = setTimeout(async () => {
      try { await refreshConversations(); if (state.activeConversationId) await refreshMessages({ preserveScroll: true }); } catch (_) {}
    }, 120);
  }

  function connectRealtime() {
    closeRealtime(); if (!state.projectId || typeof EventSource === 'undefined') return;
    const source = new EventSource(`/api/projects/${state.projectId}/message-events`); state.source = source;
    source.addEventListener('ready', () => $('.chat-anywhere-live').classList.add('connected'));
    source.addEventListener('project-message', scheduleRealtimeRefresh);
    source.onerror = () => $('.chat-anywhere-live').classList.remove('connected');
  }

  async function sendMessage() {
    const body = textarea.value.trim(); const attachmentId = attachmentSelect.value;
    if (!body && !attachmentId) return;
    const mentionedUserIds = state.members.filter((member) => {
      const name = member.display_name || member.email;
      return state.mentionedIds.has(member.user_id) && body.includes(`@${name}`);
    }).map((member) => member.user_id);
    await bridge.api(`/api/conversations/${state.activeConversationId}/messages`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body: body || null, attachment_id: attachmentId || null, reply_to_message_id: state.replyTo?.id || null, mentioned_user_ids: mentionedUserIds }),
    });
    textarea.value = ''; attachmentSelect.value = ''; state.replyTo = null; state.mentionedIds.clear(); localStorage.removeItem(draftKey());
    $('.chat-anywhere-reply').classList.add('hidden'); await refreshConversations(); await refreshMessages();
  }

  async function toggleReaction(message, emoji) {
    const reaction = message.reactions?.find((item) => item.emoji === emoji);
    if (reaction?.reacted_by_me) {
      await bridge.api(`/api/messages/${message.id}/reactions?emoji=${encodeURIComponent(emoji)}`, { method: 'DELETE' });
    } else {
      const updated = await bridge.api(`/api/messages/${message.id}/reactions`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ emoji }),
      });
      state.messages = state.messages.map((item) => item.id === updated.id ? updated : item); renderMessages({ preserveScroll: true }); return;
    }
    await refreshMessages({ preserveScroll: true });
  }

  async function showPins() {
    pinsPanel.classList.toggle('hidden'); if (pinsPanel.classList.contains('hidden')) return;
    const pins = await bridge.api(`/api/conversations/${state.activeConversationId}/pinned-messages`); pinsPanel.replaceChildren();
    const title = element('header'); title.append(element('strong', '', 'Закреплённые'), element('button', '', '×')); title.querySelector('button').type = 'button'; title.querySelector('button').addEventListener('click', () => pinsPanel.classList.add('hidden')); pinsPanel.append(title);
    for (const pin of pins) {
      const button = element('button', '', pin.body || pin.attachment_name || 'Сообщение'); button.type = 'button';
      button.addEventListener('click', () => { pinsPanel.classList.add('hidden'); messageList.querySelector(`[data-message-id="${pin.id}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }); }); pinsPanel.append(button);
    }
    if (!pins.length) pinsPanel.append(element('p', '', 'Закреплённых сообщений нет'));
  }

  conversationList.addEventListener('click', (event) => {
    const button = event.target.closest('[data-conversation-id]'); if (button) openConversation(button.dataset.conversationId).catch(showError);
  });
  $('.chat-anywhere-sidebar input').addEventListener('input', renderConversations);
  $('.chat-anywhere-conversations-toggle').addEventListener('click', () => shell.classList.toggle('show-conversations'));
  launcher.addEventListener('click', () => openWindow());
  shell.addEventListener('click', (event) => {
    const control = event.target.closest('[data-chat-control]')?.dataset.chatControl;
    if (control === 'close') closeWindow();
    else if (control === 'minimize') setMode(layout.mode === 'minimized' ? 'floating' : 'minimized');
    else if (control === 'dock') setMode(layout.mode === 'docked' ? 'floating' : 'docked');
    else if (control === 'full') bridge.navigate('messages', true);
    const action = event.target.closest('[data-chat-action]')?.dataset.chatAction;
    if (action === 'mention') mentionPanel.classList.toggle('hidden');
    else if (action === 'pins') showPins().catch(showError);
  });
  $('.chat-anywhere-reply button').addEventListener('click', () => { state.replyTo = null; $('.chat-anywhere-reply').classList.add('hidden'); });
  mentionPanel.addEventListener('click', (event) => {
    const button = event.target.closest('[data-user-id]'); if (!button) return;
    const token = `@${button.dataset.userName}`; const start = textarea.selectionStart;
    textarea.setRangeText(`${token} `, start, textarea.selectionEnd, 'end'); state.mentionedIds.add(button.dataset.userId); mentionPanel.classList.add('hidden'); textarea.focus(); saveDraft();
  });
  composer.addEventListener('submit', (event) => { event.preventDefault(); sendMessage().catch(showError); });
  textarea.addEventListener('input', saveDraft);
  textarea.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); sendMessage().catch(showError); }
  });
  messageList.addEventListener('click', async (event) => {
    const row = event.target.closest('[data-message-id]'); const action = event.target.closest('[data-action]')?.dataset.action;
    if (!row || !action) return; const message = state.messages.find((item) => item.id === row.dataset.messageId); if (!message) return;
    try {
      if (action === 'reply') {
        state.replyTo = message; const panel = $('.chat-anywhere-reply'); panel.querySelector('strong').textContent = `${message.author.name}: ${message.body || message.attachment_name || 'Вложение'}`; panel.classList.remove('hidden'); textarea.focus();
      } else if (action === 'reaction') await toggleReaction(message, event.target.closest('[data-emoji]').dataset.emoji);
      else if (action === 'react-menu') {
        const emoji = prompt(`Реакция: ${REACTIONS.join(' ')}`, '👍'); if (REACTIONS.includes(emoji)) await toggleReaction(message, emoji);
      } else if (action === 'pin') {
        await bridge.api(`/api/messages/${message.id}/pin`, { method: message.is_pinned ? 'DELETE' : 'POST' }); await refreshMessages({ preserveScroll: true });
      } else if (action === 'edit') {
        const body = prompt('Изменить сообщение', message.body || ''); if (!body?.trim()) return;
        await bridge.api(`/api/messages/${message.id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ body: body.trim() }) }); await refreshMessages({ preserveScroll: true });
      } else if (action === 'delete' && confirm('Удалить это сообщение?')) {
        await bridge.api(`/api/messages/${message.id}`, { method: 'DELETE' }); await refreshMessages({ preserveScroll: true });
      }
    } catch (error) { showError(error); }
  });

  let drag = null;
  $('.chat-anywhere-head').addEventListener('pointerdown', (event) => {
    if (layout.mode !== 'floating' || event.target.closest('button')) return;
    const bounds = shell.getBoundingClientRect(); drag = { x: event.clientX, y: event.clientY, left: bounds.left, top: bounds.top };
    shell.setPointerCapture(event.pointerId); shell.classList.add('dragging');
  });
  $('.chat-anywhere-head').addEventListener('pointermove', (event) => {
    if (!drag) return;
    shell.style.left = `${Math.max(8, Math.min(innerWidth - shell.offsetWidth - 8, drag.left + event.clientX - drag.x))}px`;
    shell.style.top = `${Math.max(8, Math.min(innerHeight - 70, drag.top + event.clientY - drag.y))}px`;
  });
  $('.chat-anywhere-head').addEventListener('pointerup', () => { if (drag) { drag = null; shell.classList.remove('dragging'); persistLayout(); } });
  window.addEventListener('mouseup', () => { if (!shell.classList.contains('hidden')) persistLayout(); });
  window.addEventListener('resize', applyLayout);
  window.addEventListener('keydown', (event) => {
    if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === 'm') { event.preventDefault(); state.opened ? closeWindow() : openWindow(); }
  });

  bus.on('context:change', (context) => loadProject(context).catch(showError));
  bus.on('chat:open', ({ conversationId, context }) => openWindow(conversationId, context));
  bus.on('route:change', ({ page, params }) => { if (page === 'messages' && params.conversation) openWindow(params.conversation); });
  applyLayout(); loadProject(state.context).catch(showError);

  return { open: openWindow, close: closeWindow, destroy: () => { closeRealtime(); launcher.remove(); shell.remove(); } };
}
