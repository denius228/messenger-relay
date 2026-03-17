self.addEventListener('push', function(event) {
    // Получаем данные от сервера (кто написал)
    const data = event.data ? event.data.json() : {};
    const title = data.title || "Secure Chat";
    
    const options = {
        body: data.body || "У вас новое зашифрованное сообщение",
        icon: "https://cdn-icons-png.flaticon.com/512/1041/1041916.png",
        badge: "https://cdn-icons-png.flaticon.com/512/1041/1041916.png",
        vibrate: [200, 100, 200, 100, 200], // Тройная вибрация
        data: { url: "/" } // Куда перекинуть при клике
    };

    event.waitUntil(self.registration.showNotification(title, options));
});

// Что делать при клике на уведомление (открываем чат)
self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: 'window' }).then(windowClients => {
            if (windowClients.length > 0) {
                windowClients[0].focus();
            } else {
                clients.openWindow(event.notification.data.url);
            }
        })
    );
});