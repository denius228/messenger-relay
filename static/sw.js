self.addEventListener('push', function(event) {
    console.log("Получен PUSH в фоне!");
    
    // Пытаемся распарсить данные (или берем стандартные)
    const data = event.data ? event.data.json() : {};
    const title = data.title || "Secure Chat";
    
    const options = {
        body: data.body || "Новое зашифрованное сообщение",
        icon: "https://cdn-icons-png.flaticon.com/512/1041/1041916.png",
        badge: "https://cdn-icons-png.flaticon.com/512/1041/1041916.png",
        vibrate: [200, 100, 200, 100, 200, 100, 400], // Агрессивная вибрация
        requireInteraction: true, // Запрещает Андроиду скрывать пуш, пока юзер не нажмет!
        renotify: true, // Разрешает вибрировать КАЖДЫЙ раз при новом сообщении
        tag: "secure-chat-msg", // Группирует пуши, чтобы не засорять шторку
        data: { url: "/" }
    };

    // Главная магия: заставляем Android показать уведомление немедленно
    const notificationPromise = self.registration.showNotification(title, options);
    event.waitUntil(notificationPromise);
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
            for (let i = 0; i < windowClients.length; i++) {
                const client = windowClients[i];
                if (client.url.includes(self.location.origin) && 'focus' in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(event.notification.data.url);
            }
        })
    );
});