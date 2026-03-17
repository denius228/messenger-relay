self.addEventListener('push', function(event) {
    console.log("Получен PUSH в фоне!");
    
    let data = {};
    try {
        if (event.data) {
            data = event.data.json();
        }
    } catch (e) {
        console.error("Ошибка парсинга пуш-данных:", e);
        // Если данные пришли не в JSON формате
        data = { body: event.data.text() };
    }
    
    const title = data.title || "Secure Chat";
    
    const options = {
        body: data.body || "Новое зашифрованное сообщение",
        icon: "https://cdn-icons-png.flaticon.com/512/1041/1041916.png",
        badge: "https://cdn-icons-png.flaticon.com/512/1041/1041916.png",
        vibrate: [200, 100, 200, 100, 200, 100, 400], // Агрессивная вибрация
        requireInteraction: true, // Пуш висит, пока пользователь не нажмет или не смахнет
        data: { url: "/" } // Куда переходить при клике
    };

    // Показываем уведомление
    const notificationPromise = self.registration.showNotification(title, options);
    event.waitUntil(notificationPromise);
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windowClients => {
            for (let i = 0; i < windowClients.length; i++) {
                const client = windowClients[i];
                // Если вкладка чата уже открыта - просто переключаем на нее фокус
                if (client.url.includes(self.location.origin) && 'focus' in client) {
                    return client.focus();
                }
            }
            // Если браузер был закрыт - открываем новую вкладку с чатом
            if (clients.openWindow) {
                return clients.openWindow(event.notification.data.url);
            }
        })
    );
});