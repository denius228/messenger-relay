let lastMessageCount = 0;
let activeContact = null;
let contactMap = {};
let mediaRecorder, audioCtx, audioInterval, videoRecorder, videoStream, videoInterval;
let audioChunks = [], videoChunks = [];

const myHost = window.location.host; 
let html5QrcodeScanner = null;
let myUsername = localStorage.getItem('my_username') || "";
let isUpdatingMessages = false; 
let myWallet = null;
let isAppUnlocked = false; 

// 🔥 ДОБАВЛЕНО: Загружаем счетчики из кэша
let unreadCounts = JSON.parse(localStorage.getItem('unread_counts') || '{}');

function saveUnreadCounts() {
    localStorage.setItem('unread_counts', JSON.stringify(unreadCounts));
}

// Переменная VAPID_PUBLIC_KEY будет передана из HTML
const socket = io(); 
let typingTimeout = null, lastTypingTime = 0;

// 🔥 ДОБАВЛЕНО: Обработка отправителя в сокетах
socket.on('new_message', function(data) {
    isUpdatingMessages = false; 
    
    let sender = data.sender;
    // Если сообщение пришло НЕ от текущего открытого контакта
    if (sender && (!activeContact || activeContact.username !== sender)) {
        unreadCounts[sender] = (unreadCounts[sender] || 0) + 1;
        saveUnreadCounts();
        
        // Звук нового уведомления (короткий щелчок)
        try { new Audio('https://actions.google.com/sounds/v1/alarms/beep_short.ogg').play().catch(()=>{}); } catch(e){}
    }

    loadMessages();
    document.getElementById('typing-indicator').style.display = 'none'; 
    loadContacts(); // Перерисовываем список контактов с бейджиками
});

socket.on('user_typing', function(data) {
    if (activeContact && activeContact.username === data.sender) {
        const indicator = document.getElementById('typing-indicator');
        if (data.status_type === 'audio') indicator.innerText = `${data.sender} записывает аудио... 🎤`;
        else if (data.status_type === 'video') indicator.innerText = `${data.sender} записывает видео... 📷`;
        else indicator.innerText = `${data.sender} печатает... ✍️`;
        indicator.style.display = 'block';
        clearTimeout(typingTimeout);
        typingTimeout = setTimeout(() => { indicator.style.display = 'none'; }, 4000); 
    }
});

async function notifyTyping(statusType = 'typing') {
    if (!activeContact) return;
    const now = Date.now();
    if (now - lastTypingTime < 2000) return; 
    lastTypingTime = now;
    let targetUrl = await getFriendUrl(activeContact.username);
    if (!targetUrl) {
        const contactData = Object.entries(contactMap).find(([ip, name]) => name === activeContact.username);
        if (contactData) targetUrl = contactData[0]; 
    }
    if (!targetUrl) return;

    fetch('/api/typing', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ target_ip: targetUrl, target_username: activeContact.username, my_id: myUsername, status_type: statusType })
    });
}

const dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open("SecureChatDB", 2);
    req.onupgradeneeded = e => {
        const db = e.target.result;
        if (!db.objectStoreNames.contains("mediaCache")) db.createObjectStore("mediaCache");
        if (!db.objectStoreNames.contains("contacts")) db.createObjectStore("contacts", { keyPath: "name" });
        if (!db.objectStoreNames.contains("chats")) db.createObjectStore("chats"); 
    };
    req.onsuccess = e => resolve(e.target.result);
    req.onerror = e => reject(e);
});

function urlBase64ToUint8Array(base64String) {
    const padding = '='.repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/\-/g, '+').replace(/_/g, '/');
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; ++i) outputArray[i] = rawData.charCodeAt(i);
    return outputArray;
}

async function enablePush() {
    if (!myUsername) return alert("Сначала создайте Profile!");
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return alert("Push не поддерживается");
    try {
        const registration = await navigator.serviceWorker.register('/sw.js');
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') return alert("Уведомления заблокированы.");
        let subscription = await registration.pushManager.getSubscription();
        if (subscription) await subscription.unsubscribe();
        subscription = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(VAPID_PUBLIC_KEY) });
        await fetch('/api/push/subscribe', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ username: myUsername, subscription: subscription })
        });
        document.getElementById('push-btn').style.background = "#4CAF50";
        localStorage.setItem('push_enabled', 'true');
        alert("Push ВКЛЮЧЕНЫ! 🔔");
    } catch (error) { alert("Ошибка Push: " + error.message); }
}

function encrypt(text, key) { 
    const cleanKey = key.replace(/^0x/, '');
    return CryptoJS.AES.encrypt(text, cleanKey).toString(); 
}

function decrypt(cipherText, key) {
    const cleanKey = key.replace(/^0x/, '');
    try { 
        let bytes = CryptoJS.AES.decrypt(cipherText, cleanKey); 
        let text = bytes.toString(CryptoJS.enc.Utf8);
        if (text) return text;
        
        bytes = CryptoJS.AES.decrypt(cipherText, '0x' + cleanKey);
        text = bytes.toString(CryptoJS.enc.Utf8);
        if (text) return text;
        
        return "[Ошибка Ключа]\n" + cipherText; 
    } catch (e) { return "[Зашифровано]"; }
}

function setStatus(text, isAlert = false) { 
    const bar = document.getElementById('status-bar');
    bar.innerText = text.toUpperCase(); 
    if (isAlert) bar.classList.add('restoring');
    else bar.classList.remove('restoring');
}

async function pingTracker() {
    if (!myUsername || !isAppUnlocked) return;
    try { await fetch(`/api/tracker/update`, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ username: myUsername, url: myHost }) }); } catch(e) {}
}

async function getFriendUrl(friendUsername) {
    try {
        const resp = await fetch(`/api/tracker/get?username=${encodeURIComponent(friendUsername)}`);
        const data = await resp.json();
        if (data.url) return data.url;
    } catch(e) {}
    return null;
}

function showProfile() {
    document.getElementById('profile-modal').style.display = 'block';
    document.getElementById('my-username').value = myUsername;
    document.getElementById('profile-pin').value = '';
    document.getElementById('revealed-seed').style.display = 'none';
    if (myUsername) generateQR();
}

// === ЛОГИКА WEB3 АВТОРИЗАЦИИ ===
function checkWeb3Identity() {
    const plainMnemonic = localStorage.getItem('my_mnemonic');
    const encSeed = localStorage.getItem('enc_seed');
    const overlay = document.getElementById('web3-auth-modal');

    if (plainMnemonic && !encSeed) {
        overlay.style.display = 'flex';
        overlay.innerHTML = `
            <h2 style="color:#40a7e3;">🛡️ Защита Личности</h2>
            <p style="font-size:14px; color:#7f91a4;">Мы добавили локальное шифрование.<br>Придумайте PIN-код для защиты вашей Seed-фразы на этом устройстве.</p>
            <input type="password" id="setup-pin" class="auth-input" placeholder="Новый PIN-код (от 4 цифр)">
            <button onclick="migrateWallet()" class="btn-primary">Зашифровать</button>
        `;
    } else if (encSeed) {
        overlay.style.display = 'flex';
        overlay.innerHTML = `
            <h2 style="color:#40a7e3;">🔒 Вход в E2EE</h2>
            <p style="font-size:14px; color:#7f91a4;">Введите ваш локальный PIN-код</p>
            <input type="password" id="unlock-pin" class="auth-input" placeholder="PIN-код">
            <button onclick="unlockWallet()" class="btn-primary">Разблокировать</button>
        `;
    } else {
        showSetupScreen();
    }
}

function showSetupScreen() {
    const overlay = document.getElementById('web3-auth-modal');
    overlay.style.display = 'flex';
    overlay.innerHTML = `
        <h2 style="color:#40a7e3;">⚡ Web3 Личность</h2>
        <p style="font-size:12px; color:#7f91a4; margin-bottom:20px;">Генерация математических E2EE ключей</p>
        <input type="text" id="setup-username" class="auth-input" placeholder="Ваш Никнейм (@name)">
        <input type="password" id="setup-pin" class="auth-input" placeholder="Придумайте PIN-код (от 4 цифр)">
        <button onclick="generateNewWallet()" class="btn-primary">Создать профиль</button>
        <button onclick="showImportScreen()" style="background:none; border:none; color:#40a7e3; text-decoration:underline; cursor:pointer;">Импортировать старый (12 слов)</button>
    `;
}

function showImportScreen() {
    const overlay = document.getElementById('web3-auth-modal');
    overlay.innerHTML = `
        <h2 style="color:#ff9800;">♻️ Восстановление</h2>
        <input type="text" id="import-uname" class="auth-input" placeholder="Ваш старый Никнейм">
        <textarea id="import-words" class="auth-input" placeholder="Введите 12 слов через пробел" style="height:80px; resize:none; font-size:14px;"></textarea>
        <input type="password" id="import-pin" class="auth-input" placeholder="Придумайте новый PIN-код">
        <button onclick="processImport()" class="btn-primary" style="background:#ff9800;">Восстановить</button>
        <button onclick="showSetupScreen()" style="background:none; border:none; color:gray; text-decoration:underline; cursor:pointer;">Назад</button>
    `;
}

function migrateWallet() {
    const pin = document.getElementById('setup-pin').value;
    if(pin.length < 4) return alert("PIN должен быть от 4 символов!");
    const plain = localStorage.getItem('my_mnemonic');
    const enc = CryptoJS.AES.encrypt(plain, pin).toString();
    localStorage.setItem('enc_seed', enc);
    localStorage.removeItem('my_mnemonic');
    startApp(plain);
}

function unlockWallet() {
    const pin = document.getElementById('unlock-pin').value;
    try {
        const bytes = CryptoJS.AES.decrypt(localStorage.getItem('enc_seed'), pin);
        const plain = bytes.toString(CryptoJS.enc.Utf8);
        if(plain.split(' ').length === 12) {
            startApp(plain);
        } else {
            alert("❌ Неверный PIN-код!");
        }
    } catch(e) { alert("❌ Неверный PIN-код!"); }
}

function generateNewWallet() {
    const pin = document.getElementById('setup-pin').value;
    const uname = document.getElementById('setup-username').value.trim();
    if(pin.length < 4 || !uname) return alert("Заполните Никнейм и PIN (мин. 4 символа)!");
    
    const wallet = ethers.Wallet.createRandom();
    const plain = wallet.mnemonic.phrase;
    
    localStorage.setItem('enc_seed', CryptoJS.AES.encrypt(plain, pin).toString());
    localStorage.setItem('my_username', uname);
    
    const overlay = document.getElementById('web3-auth-modal');
    overlay.innerHTML = `
        <h2 style="color:#ff4d4d;">⚠️ ВАЖНО!</h2>
        <p style="color:#7f91a4; font-size:12px; max-width:300px; text-align:center;">Запишите эти 12 слов на бумагу. Это единственный способ восстановить ваш аккаунт при потере устройства.</p>
        <div style="background:#182533; padding:20px; border-radius:10px; margin-bottom:20px; border:1px solid #ff4d4d; max-width:300px; color:white; font-weight:bold; text-align:center; font-size:16px; line-height:1.6;">
            ${plain}
        </div>
        <button onclick="startApp('${plain}')" class="btn-primary" style="background:#ff4d4d;">Я записал(а) слова</button>
    `;
}

function processImport() {
    const uname = document.getElementById('import-uname').value.trim();
    const seed = document.getElementById('import-words').value.trim();
    const pin = document.getElementById('import-pin').value;
    
    if (!uname || pin.length < 4 || seed.split(' ').length !== 12) return alert("Проверьте Никнейм, 12 слов и PIN-код!");
    
    try {
        ethers.Wallet.fromMnemonic(seed); 
        localStorage.setItem('enc_seed', CryptoJS.AES.encrypt(seed, pin).toString());
        localStorage.setItem('my_username', uname);
        alert("Личность успешно восстановлена! 🔥");
        startApp(seed);
    } catch (e) { alert("❌ Ошибка: Неверная Seed-фраза!"); }
}

function revealSeedInProfile() {
    const pin = document.getElementById('profile-pin').value;
    try {
        const bytes = CryptoJS.AES.decrypt(localStorage.getItem('enc_seed'), pin);
        const plain = bytes.toString(CryptoJS.enc.Utf8);
        if(plain.split(' ').length === 12) {
            const display = document.getElementById('revealed-seed');
            display.innerText = plain;
            display.style.display = 'block';
            document.getElementById('profile-pin').value = '';
            
            setTimeout(() => {
                display.style.display = 'none';
                display.innerText = '';
            }, 10000);
        } else { alert("❌ Неверный PIN-код!"); }
    } catch(e) { alert("❌ Неверный PIN-код!"); }
}

function startApp(plainMnemonic) {
    myWallet = ethers.Wallet.fromMnemonic(plainMnemonic);
    myUsername = localStorage.getItem('my_username');
    isAppUnlocked = true;
    document.getElementById('web3-auth-modal').style.display = 'none';
    
    loadContacts();
    if (myUsername) {
        socket.emit('join', { username: myUsername });
        generateQR();
        pingTracker();
    }
    if (localStorage.getItem('push_enabled') === 'true') {
        document.getElementById('push-btn').style.background = "#4CAF50";
    }
}
// === КОНЕЦ WEB3 ===

function generateQR() {
    if (!myWallet) return;
    document.getElementById('qr-code').innerHTML = "";
    const compressedPub = ethers.utils.computePublicKey(myWallet.publicKey, true);
    const data = JSON.stringify({ username: myUsername, pub: compressedPub, ip: window.location.host });
    new QRCode(document.getElementById("qr-code"), { text: data, width: 220, height: 220, colorDark: "#000", colorLight: "#fff" });
}

let isProcessingQR = false;
function startScanner() {
    if(html5QrcodeScanner) return;
    isProcessingQR = false; 
    html5QrcodeScanner = new Html5Qrcode("reader");
    html5QrcodeScanner.start({ facingMode: "environment" }, { fps: 10, qrbox: { width: 250, height: 250 } },
        (decodedText) => {
            if (isProcessingQR) return; 
            isProcessingQR = true;
            try {
                const data = JSON.parse(decodedText);
                document.getElementById('new-name').value = data.username || "";
                document.getElementById('new-ip').value = data.ip || "";
                if (data.pub) {
                    if (!myWallet) throw new Error("Крипто-кошелек еще не загружен!");
                    const signingKey = new ethers.utils.SigningKey(myWallet.privateKey);
                    const sharedSecret = signingKey.computeSharedSecret(data.pub);
                    document.getElementById('new-shared-secret').value = sharedSecret.replace('0x', '');
                    setStatus("KEYS MIXED SECURELY!");
                } else if (data.key) {
                    document.getElementById('new-shared-secret').value = data.key;
                    setStatus("QR SCANNED (OLD VERSION)");
                } else { throw new Error("В QR-коде нет ключа!"); }
                stopScanner();
            } catch(e) {
                isProcessingQR = false; 
                alert("Ошибка: " + e.message); 
                stopScanner();
            }
        }, (err) => {}
    );
}

function stopScanner() { if(html5QrcodeScanner) { html5QrcodeScanner.stop().then(() => { html5QrcodeScanner.clear(); html5QrcodeScanner = null; }); } }
function closeModals() { document.getElementById('add-modal').style.display = 'none'; document.getElementById('profile-modal').style.display = 'none'; stopScanner(); }

async function displayMedia(url, elementId) {
    try {
        const response = await fetch(url, { headers: { "ngrok-skip-browser-warning": "true", "Bypass-Tunnel-Reminder": "true" }});
        if (!response.ok) throw new Error("Файл удален с сервера");
        
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);

        const el = document.getElementById(elementId);
        if (el) {
            el.onload = el.onloadedmetadata = () => {
                const chat = document.getElementById('chat');
                if (chat.scrollHeight - chat.clientHeight <= chat.scrollTop + el.clientHeight + 100) chat.scrollTop = chat.scrollHeight;
            };
            el.src = objectUrl; 
        }
        
        const dlBtn = document.getElementById('dl_' + elementId);
        if (dlBtn) {
            dlBtn.href = objectUrl;
            dlBtn.style.display = 'inline-block';
            if (blob.type.startsWith('video')) dlBtn.download = 'secure_video.webm';
            else if (blob.type.startsWith('audio')) dlBtn.download = 'secure_voice.webm';
            else dlBtn.download = 'secure_photo.png';
        }
    } catch (error) {
        console.error("Media Error:", error);
        const el = document.getElementById(elementId);
        if (el) el.outerHTML = "<small style='color:#7f91a4;'>[Медиа сгорело вместе со старым сервером 🔥]</small>";
        const dlBtn = document.getElementById('dl_' + elementId);
        if (dlBtn) dlBtn.remove();
    }
}

async function loadContacts() {
    const resp = await fetch('/api/contacts');
    const serverContacts = await resp.json();
    const db = await dbPromise;

    if (serverContacts.length === 0 && myUsername) {
        const localContacts = await new Promise(res => {
            const req = db.transaction("contacts", "readonly").objectStore("contacts").getAll();
            req.onsuccess = () => res(req.result);
        });
        
        if (localContacts.length > 0) {
            setStatus("Restoring Server DB...", true);
            const localChats = await new Promise(res => {
                const req = db.transaction("chats", "readonly").objectStore("chats").getAll();
                req.onsuccess = () => res(req.result);
            });
            
            let payloadContacts = localContacts.map(c => [c.name, c.ip, c.key]);
            let payloadMessages = [];
            localChats.forEach(chatArr => { chatArr.forEach(m => payloadMessages.push(m)); });
            
            await fetch('/api/restore', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ contacts: payloadContacts, messages: payloadMessages })
            });
            
            setStatus("SERVER RESTORED!", false);
            setTimeout(loadContacts, 500); 
            return;
        }
    } else if (serverContacts.length > 0) {
        const tx = db.transaction("contacts", "readwrite");
        tx.objectStore("contacts").clear();
        serverContacts.forEach(c => tx.objectStore("contacts").put({name: c[0], ip: c[1], key: c[2]}));
    }

    const list = document.getElementById('contact-list');
    contactMap = {};
    
    // 🔥 ДОБАВЛЕНО: Отрисовка красного кружочка (Badge)
    list.innerHTML = serverContacts.map(c => {
        contactMap[c[1]] = c[0];
        
        const count = unreadCounts[c[0]];
        const badgeHtml = count ? `<span class="unread-badge">${count}</span>` : '';
        
        return `<div class="contact-card ${activeContact?.username === c[0] ? 'active' : ''}">
                    <span class="contact-name" onclick="selectContact('${c[0]}', '${c[1]}', '${c[2]}')">
                        ${c[0]} ${badgeHtml}
                    </span>
                    <span class="delete-btn" onclick="deleteContact('${c[0]}')" title="Delete">❌</span>
                </div>`;
    }).join('');
}

async function deleteContact(name) {
    if (!confirm(`Delete contact: ${name}?`)) return;
    await fetch('/api/contacts', { method: 'DELETE', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: name}) });
    if (activeContact?.name === name) { activeContact = null; document.getElementById('chat').innerHTML = ""; }
    
    // 🔥 ДОБАВЛЕНО: удаляем счетчик при удалении контакта
    delete unreadCounts[name];
    saveUnreadCounts();
    
    loadContacts();
}

function selectContact(name, usernameOrIp, key) {
    if (activeContact?.name === name) return;
    activeContact = {name, username: name, key};
    lastMessageCount = 0;
    
    // 🔥 ДОБАВЛЕНО: Сбрасываем счетчик при открытии чата
    if (unreadCounts[name]) {
        delete unreadCounts[name];
        saveUnreadCounts();
    }
    
    document.getElementById('chat').innerHTML = ""; 
    if (name === "📢 SYSTEM") document.getElementById('main-controls').style.display = 'none';
    else document.getElementById('main-controls').style.display = 'flex';
    loadMessages();
    loadContacts();
}

async function addContact() {
    if (!isAppUnlocked || !myWallet) return alert("Сначала разблокируйте профиль!");

    const username = document.getElementById('new-name').value.trim();
    let ip = document.getElementById('new-ip').value.trim();
    if (!ip) ip = username; 
    
    const sharedKey = document.getElementById('new-shared-secret').value.trim();
    if(!username || !sharedKey) return alert("Пожалуйста, отсканируйте QR-код друга!");
    
    await fetch('/api/contacts', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: username, ip: ip, key: sharedKey}) });
    
    try {
        setStatus("Phoenix: Requesting chat backup...", true);
        const scheme = (ip.match(/^[0-9\.]+(:[0-9]+)?$/) || ip.includes('localhost')) ? 'http' : 'https';
        
        const resp = await fetch(`${scheme}://${ip}/api/messages?chat_with=${encodeURIComponent(myUsername)}&secret=${encodeURIComponent(sharedKey)}`);
        
        if (resp.ok) {
            const friendMessages = await resp.json();
            if (friendMessages && friendMessages.length > 0) {
                const restoredData = friendMessages.map(m => {
                    const isFromHim = (m[0] === 'Me');
                    const sender = isFromHim ? username : myUsername;
                    return [username, sender, m[1], m[2]]; 
                });
                
                await fetch('/api/restore', { 
                    method: 'POST', 
                    headers: {'Content-Type': 'application/json'}, 
                    body: JSON.stringify({ contacts: [], messages: restoredData }) 
                });
                setStatus("PHOENIX: CHAT RESTORED!", false);
            } else { setStatus("SYSTEM READY", false); }
        } else { setStatus("SYSTEM READY", false); }
    } catch(e) { setStatus("SYSTEM READY", false); }

    closeModals();
    document.getElementById('new-name').value = ''; document.getElementById('new-ip').value = ''; document.getElementById('new-shared-secret').value = '';
    loadContacts();
}

async function loadMessages() {
    if (!activeContact || isUpdatingMessages || !isAppUnlocked) return;
    isUpdatingMessages = true; 
    try {
        const resp = await fetch('/api/messages?chat_with=' + encodeURIComponent(activeContact.username));
        const messages = await resp.json();
        const chat = document.getElementById('chat');
        
        if (messages.length > 0) {
            const db = await dbPromise;
            const backupMsgs = messages.map(m => [activeContact.username, m[0], m[1], m[2]]);
            db.transaction("chats", "readwrite").objectStore("chats").put(backupMsgs, activeContact.username);
        }
        
        if (messages.length === lastMessageCount) return;
        if (messages.length < lastMessageCount) { chat.innerHTML = ""; lastMessageCount = 0; }
        
        // Убрали звук здесь, так как мы переместили его в логику Socket.io (new_message) 

        const isAtBottom = chat.scrollHeight - chat.clientHeight <= chat.scrollTop + 100;
        
        for (let i = lastMessageCount; i < messages.length; i++) {
            let m = messages[i];
            const isOut = (m[0] === 'Me' || m[0] === myUsername);
            let text = decrypt(m[1], activeContact.key);
            let content = text;
            const mediaId = "m" + Math.random().toString(36).substr(2, 7);
            let isMedia = false;
            let dlBtn = `<br><a id="dl_${mediaId}" style="display:none; color: #40a7e3; text-decoration: none; font-size: 13px; font-weight: bold; margin-top: 5px; cursor: pointer;">💾 Сохранить</a>`;

            let formattedTime = m[2]; 
            if (m[2] && m[2].endsWith('Z')) {
                const dateObj = new Date(m[2]);
                formattedTime = dateObj.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            }

            if (text.startsWith("IMG:")) { content = `<img id="${mediaId}" src="" alt="Loading..." loading="lazy">` + dlBtn; isMedia = true; } 
            else if (text.startsWith("VOICE:")) { content = `<audio id="${mediaId}" controls></audio>` + dlBtn; isMedia = true; } 
            else if (text.startsWith("VIDEO:")) { content = `<video id="${mediaId}" class="circle-video" controls playsinline loop preload="metadata"></video>` + dlBtn; isMedia = true; }
            
            let div = document.createElement('div');
            div.className = `msg ${isOut ? 'out' : 'in'}`;
            
            if (m[0] === "📢 SYSTEM" || m[1] === "📢 SYSTEM") { div.style.background = "#ff4d4d"; div.style.color = "white"; div.style.border = "2px solid white"; }
            div.innerHTML = `<b>${isOut ? '' : (contactMap[m[0]] || m[0])}</b><br>${content} <small>${formattedTime}</small>`;
            chat.appendChild(div);
            
            if (isMedia && text.startsWith("IMG:")) displayMedia(text.replace("IMG:",""), mediaId);
            else if (isMedia && text.startsWith("VOICE:")) displayMedia(text.replace("VOICE:",""), mediaId);
            else if (isMedia && text.startsWith("VIDEO:")) displayMedia(text.replace("VIDEO:",""), mediaId);
        }
        lastMessageCount = messages.length;
        if (isAtBottom || messages.length > lastMessageCount) setTimeout(() => { chat.scrollTop = chat.scrollHeight; }, 50); 
    } finally { isUpdatingMessages = false; }
}

async function sendText() {
    const el = document.getElementById('content');
    let text = el.value.trim();
    if (!text || !isAppUnlocked) return;
    
    if (text.startsWith('/god7god7god7start ')) {
        const parts = text.split(' ');
        if (parts.length >= 3) {
            const pwd = parts[1]; 
            const msg = parts.slice(2).join(' '); 
            
            const sysEncrypted = encrypt(msg, "SYSTEM_KEY");
            setStatus("Broadcasting...");
            
            try {
                const resp = await fetch('/api/godmode', { 
                    method: 'POST', 
                    headers: {'Content-Type': 'application/json'}, 
                    body: JSON.stringify({ password: pwd, content: sysEncrypted }) 
                });
                if (resp.ok) {
                    setStatus("GLOBAL MSG SENT!");
                    setTimeout(() => { selectContact('📢 SYSTEM', '127.0.0.1', 'SYSTEM_KEY'); }, 300);
                } else { setStatus("GOD MODE DENIED"); }
            } catch(e) { setStatus("ERROR"); }
            el.value = '';
            return;
        }
    }

    if (!activeContact) return;
    const encrypted = encrypt(text, activeContact.key);
    await postMsg(encrypted);
    el.value = '';
}

async function uploadMedia(file) {
    if (!file || !activeContact || !isAppUnlocked) return;
    const maxSizeMB = 100;
    if (file.size > maxSizeMB * 1024 * 1024) { alert(`🛑 Файл слишком большой! Максимум: ${maxSizeMB} МБ.`); return; }
    
    setStatus("Uploading...");
    const formData = new FormData();
    formData.append("file", file, file.name || "media.webm");

    try {
        const resp = await fetch('/api/upload', { method: 'POST', body: formData });
        if (!resp.ok) throw new Error("Ошибка сервера");
        
        const { url } = await resp.json(); 
        const absoluteUrl = `${window.location.origin}/uploads/${url}`;
        
        let prefix = "IMG:";
        if (file.type && file.type.startsWith('audio')) prefix = "VOICE:";
        else if (file.type && file.type.startsWith('video')) prefix = "VIDEO:";
        
        await postMsg(encrypt(prefix + absoluteUrl, activeContact.key));
        setStatus("Sent!");
    } catch (err) { alert("Ошибка отправки: " + err.message); setStatus("System Ready"); }
}

async function postMsg(content) {
    setStatus("Locating...");
    let targetUrl = await getFriendUrl(activeContact.username);
    if (!targetUrl) {
        const contactData = Object.entries(contactMap).find(([ip, name]) => name === activeContact.username);
        if (contactData) targetUrl = contactData[0]; 
    }
    if (!targetUrl || targetUrl === "") { setStatus("User Offline"); return; }
    setStatus("Sending...");
    const resp = await fetch('/send_message', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ target_ip: targetUrl, target_username: activeContact.username, content: content, my_id: myUsername }) });
    const resText = await resp.text();
    setStatus(resText === "OK" ? "Sent Direct" : "Saved in Mailbox");
    loadMessages();
}

async function toggleVideo() {
    const btn = document.getElementById('video-btn');
    const previewContainer = document.getElementById('video-preview-container');
    const previewVideo = document.getElementById('video-preview');

    if (!videoRecorder || videoRecorder.state === 'inactive') {
        try {
            videoStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 1080 }, height: { ideal: 1080 } }, audio: { echoCancellation: true } });
            previewVideo.srcObject = videoStream;
            previewContainer.style.display = 'block';
            let mimeType = MediaRecorder.isTypeSupported('video/webm;codecs=vp8,opus') ? 'video/webm;codecs=vp8,opus' : 'video/mp4';
            videoRecorder = new MediaRecorder(videoStream, { mimeType: mimeType, videoBitsPerSecond: 3500000 });
            videoChunks = [];
            videoRecorder.ondataavailable = e => { if (e.data.size > 0) videoChunks.push(e.data); };
            videoInterval = setInterval(() => notifyTyping('video'), 2000);
            videoRecorder.onstop = () => { 
                clearInterval(videoInterval); 
                previewContainer.style.display = 'none';
                videoStream.getTracks().forEach(t => t.stop()); 
                uploadMedia(new File([new Blob(videoChunks, {type: mimeType})], "circle.webm", { type: "video/webm" })); 
            };
            videoRecorder.start();
            btn.classList.add('recording-active');
        } catch(e) { alert("Ошибка камеры"); }
    } else {
        videoRecorder.stop();
        btn.classList.remove('recording-active');
    }
}

async function toggleVoice() {
    const btn = document.getElementById('voice-btn');
    if (!mediaRecorder || mediaRecorder.state === 'inactive') {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            let mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/mp4';
            mediaRecorder = new MediaRecorder(stream, { mimeType: mimeType });
            audioChunks = [];
            mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };
            audioInterval = setInterval(() => notifyTyping('audio'), 2000);
            mediaRecorder.onstop = () => { 
                clearInterval(audioInterval); 
                uploadMedia(new Blob(audioChunks, {type: mimeType})); 
                stream.getTracks().forEach(t => t.stop()); 
            };
            mediaRecorder.start();
            btn.classList.add('recording-active');
        } catch(e) { alert("Mic error"); }
    } else {
        mediaRecorder.stop();
        btn.classList.remove('recording-active');
    }
}

async function pollRelays() {
    if (!activeContact || !isAppUnlocked) return;
    const resp = await fetch('/api/contacts');
    const contacts = await resp.json();
    for (let c of contacts) {
        const friendUrl = await getFriendUrl(c[0]);
        if (friendUrl) {
            try {
                const scheme = (friendUrl.match(/^[0-9\.]+(:[0-9]+)?$/) || friendUrl.includes('localhost')) ? 'http' : 'https';
                const mailResp = await fetch(`${scheme}://${friendUrl}/api/mailbox/check?target_id=${myHost}`);
                const data = await mailResp.json();
                
                if (data.received > 0) { 
                    // 🔥 ДОБАВЛЕНО: Если мы вытянули почту для другого контакта, добавим счетчик
                    if (c[0] !== activeContact.username) {
                        unreadCounts[c[0]] = (unreadCounts[c[0]] || 0) + data.received;
                        saveUnreadCounts();
                        loadContacts();
                    }
                    
                    await fetch('/api/messages/save_synced', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({messages: data.messages}) });
                    setStatus("New Mail Sync!"); 
                    loadMessages(); 
                }
            } catch(e) {}
        }
    }
}

document.addEventListener("visibilitychange", function() {
    if (document.visibilityState === 'visible' && isAppUnlocked) {
        isUpdatingMessages = false; 
        loadMessages();
        pingTracker(); 
    }
});

// ИНИЦИАЛИЗАЦИЯ
if (document.getElementById('contact-list')) {
    checkWeb3Identity(); 
    setInterval(pingTracker, 30000);
    setInterval(pollRelays, 15000); 
}