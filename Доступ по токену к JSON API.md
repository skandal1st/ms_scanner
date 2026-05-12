Токен для доступа к JSON API 1.2 передается разработчику при активации решения через Vendor API в момент установки или возобновления решения. Время жизни токена не ограничено.

Токен аннулируется при удалении и приостановке решения. На момент деактивации решения через Vendor API токен уже аннулирован.

Токен при доступе к JSON API 1.2 следует передавать как [Bearer-токен](https://dev.moysklad.ru/doc/api/remap/1.2/#mojsklad-json-api-obschie-swedeniq-autentifikaciq), а именно в виде заголовка HTTP-запроса:
Пример:
``` JSON
```

```
Authorization: Bearer <access_token>
Authorization: Bearer 6ab89be1ae6ff147755625ee8da948e42612233b
```

#### Диаграмма последовательности предоставления доступа при подключении решения
![[Pasted image 20260510125934.png]]


#### Диаграмма последовательности отзыва доступа при отключении решения
![[Pasted image 20260510125951.png]]

### Работа с вебхуками

Для работы с вебхуками в дескрипторе нужно указать одно из специальных прав доступа: `<useOwnWebhooks/>` или `<useAllWebhooks/>`.

Для большинства решений достаточно **useOwnWebhooks**. Такое решение сможет создавать, изменять и удалять свои вебхуки, а также просматривать вебхуки созданные другими решениями. Если решению требуется возможность изменять и удалять вебхуки других решений, используйте **useAllWebhooks**. Нельзя указать сразу оба этих права.

Подробнее про работу с правами решений можно ознакомиться в разделе **Блок access** [[Дескриптор]]

Если решение создает один или несколько вебхуков на аккаунте пользователя, то необходимо учитывать:

- Вебхуки доступны только на пробном и платных тарифах.
- Если пользователь инициирует удаление решения, перед тем как удалить решение, МойСклад удаляет все вебхуки, созданные этим решением для данного аккаунта.
- Перед приостановкой платного решения все вебхуки для данного аккаунта будут отключены, а после возобновления — включены.

Подробную информацию о работе с вебхуками по JSON API можно получить по [ссылке](https://dev.moysklad.ru/doc/api/remap/1.2/dictionaries/#suschnosti-vebhuki).

### Работа с дополнительными полями

Для работы с доп.полями в дескрипторе нужно указать одно из специальных прав доступа: `<useOwnAttributeMetadata/>` или `<useAllAttributeMetadata/>`.

Для большинства решений достаточно **useOwnAttributeMetadata**. Решение с таким правом сможет создавать, изменять и удалять свои дополнительные поля, а также просматривать доп.поля созданные другими решениями. Если решению требуется возможность изменять и удалять доп.поля других решений, используйте **useAllAttributeMetadata**. Нельзя указать сразу оба этих права.

Подробнее про работу с правами решений можно ознакомиться в разделе **Блок access** [дескриптора](https://dev.moysklad.ru/doc/api/vendor/1.0/#deskriptor-resheniq).

Подробную информацию о работе с дополнительными полями по JSON API можно получить по [ссылке](https://dev.moysklad.ru/doc/api/remap/1.2/#mojsklad-json-api-obschie-swedeniq-rabota-s-dopolnitel-nymi-polqmi).

### Особенности доступа к некоторым функциям JSON API 1.2

На данный момент для решений существуют ограничения в работе с JSON API 1.2:

- Решение не может получать [Информацию о действующей подписке компании](https://dev.moysklad.ru/doc/api/remap/1.2/dictionaries/#suschnosti-podpiska-kompanii).
- Решение не может создавать [Бонусные операции](https://dev.moysklad.ru/doc/api/remap/1.2/dictionaries/#suschnosti-bonusnaq-operaciq).
- Решение может использовать [Шаблоны документов](https://dev.moysklad.ru/doc/api/remap/1.2/documents/#dokumenty-obschie-swedeniq-shablony-dokumentow), но получаемый шаблон или шаблон на основании документа, имеет меньший набор предзаполненных полей чем шаблон полученный по авторизации пользователя.

Подробнее о том, как работать с API МоегоСклада, смотрите в [видео](https://www.youtube.com/watch?v=eQWNADSRSWA).

### Окна (iframes)

Окна решений позволяют пользователям МоегоСклада выполнять основные действия по настройке или работе с решением.

За настройку и работу окон решения отвечает [блок iframes в дескрипторе](https://dev.moysklad.ru/doc/api/vendor/1.0/#blok-iframes). В случае отсутствия этого блока считается, что у решения отсутствуют окна.

В настоящий момент поддерживается 2 типа окон:

- главное окно
- окно чатов

При реализации окон решения следует учитывать следующее:

1. По возможности используйте [UI Kit МоегоСклада](https://github.com/moysklad/html-marketplace-1.0-uikit) для визуального оформления содержимого.
2. Если в iframe будет какая-либо пользовательская функциональность кроме настроек, предусмотрите доступ к этой функциональности для пользователей с разными правами доступа, не только для администратора. Для этого используйте [контекст пользователя](https://dev.moysklad.ru/doc/api/vendor/1.0/#kontext-pol-zowatelq).

Об ограничениях содержимого окон читайте в разделе [Ограничения для контента, загружаемого в виджетах](https://dev.moysklad.ru/doc/api/vendor/1.0/#ogranicheniq-dlq-kontenta-zagruzhaemogo-w-widzhetah)

#### Главный iframe
``` html
<!doctype html>
<html>

<head>
  ...
</head>

<body>
...
<script type="text/javascript"
        src="https://apps-api.moysklad.ru/js/ns/appstore/app/v1/moysklad-iframe-expand-3.js"></script>
</body>

</html>
```

Главное окно решения — это основной iframe для настройки или работы с серверным решением, который открывается при нажатии на кнопку **Начать работу** в каталоге либо при переходе по прямой ссылке вида `https://online.moysklad.ru/app/#embed-apps?id={appId}`.

Главное окно начинает отображаться после перехода решения в статус **Activated** или **SettingsRequired**.

Если содержимое iframe не умещается в минимальную допустимую высоту окна (768px), то необходимо настроить автоматическое масштабирование высоты окна в зависимости от контента. Для этого в iframe нужно реализовать отправку сообщения `EventMessage` при любом изменении высоты контента:

- сообщение необходимо послать родительскому окну (`parent`);
- данные сообщения должны содержать свойство `height` — высоту страницы, которая сейчас отображается, в пикселях.

Для того чтобы не реализовывать это поведение самостоятельно, можно подключить следующий [js скрипт](https://apps-api.moysklad.ru/js/ns/appstore/app/v1/moysklad-iframe-expand-3.js) на свою страницу. Шаблон такой страницы приведен справа:

Более подробный пример использования главного iframe можно увидеть в [демо-решении на PHP](https://dev.moysklad.ru/doc/api/vendor/1.0/#demo-reshenie).
#### Окно чатов

Позволяет встроить отдельный iframe решения в [Чаты](https://support.moysklad.ru/hc/ru/articles/203325403-%D0%9B%D0%B5%D0%BD%D1%82%D0%B0-%D1%81%D0%BE%D0%B1%D1%8B%D1%82%D0%B8%D0%B9#h_01K8X50ZCSN54FXVYQFCQ87R9D).

Пример чатов с несколькими решениями разработчиков:
![[Pasted image 20260510130142.png]]

Окно чатов начинает отображаться только после перехода решения в статус **Activated**. Если решение предполагает настройку и статус **SettingsRequired**, окно отображается после настройки решения.

Отличия в реализации окон чатов от главных iframes:

1. Максимальная ширина: 1096 px (горизонтального скролла не должно быть)
2. Вертикальный скролл реализуется самим разработчиком (автоматическое масштабирование высоты окна не поддерживается хост-окном)
3. Не поддерживаются [сервисы хост-окна](https://dev.moysklad.ru/doc/api/vendor/1.0/#serwisy-host-okna).

### Виджеты

> Дескриптор решения с виджетом в карточке контрагента

``` xml
   <ServerApplication xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"
                xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2
         https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
       <iframe>...</iframe>
       <vendorApi>...</vendorApi>
       <access>...</access>
       <widgets>
           <entity.counterparty.edit>
               <sourceUrl>https://b2b.moysklad.ru/widget/counter-party</sourceUrl>
               <height>
                   <fixed>61px</fixed>
               </height>
               <supports>
                   <open-feedback/>
                   <save-handler/>
               </supports>
                <uses>
                    <good-folder-selector/>
                </uses>
           </entity.counterparty.edit>
       </widgets>
   </ServerApplication>
```

Виджет — это плагин, имеющий визуальную часть. Визуальная часть виджета — прямоугольный блок, который встраивается в интерфейс МоегоСклада в определенном месте. Содержимое блока определяется решением.
Виджеты можно добавить на страницы МоегоСклада, которые есть в [списке](https://dev.moysklad.ru/doc/api/vendor/1.0/#blok-widgets). Чтобы встроить виджет на страницу, которая пока не поддерживается, свяжитесь с нами в Telegram или по электронной почте.

Виджеты доступны для серверных решений с дескриптором версии v2. Пример дескриптора решения с виджетом в карточке контрагента расположен в правой части экрана. Подробнее структура дескриптора для решения с виджетом описана в разделе [[Дескриптор]].

#### Загрузка и отображение виджета на странице

Виджет на странице загружается в iframe по URL, указанному в теге `<sourceUrl>...</sourceUrl>` виджета в дескрипторе, с передачей текущего [контекста пользователя](https://dev.moysklad.ru/doc/api/vendor/1.0/#kontext-pol-zowatelq).

Виджет начинает отображаться на странице только после перехода решения в статус **Activated**. Если решение предполагает настройку и статус **SettingsRequired**, виджет отображается после настройки решения.

После того как пользователь установил и настроил решение с дескриптором из примера выше, виджет будет показан в карточке контрагента.
![[Pasted image 20260510130300.png]]

Виджеты могут отображаться в нескольких режимах:

- развернутый вид — виджет отображается с рабочей iframe-областью;
- свернутый вид — рабочая область виджета скрыта;
- скрыт — элемент управления виджетом не отображается (пользователь не может взаимодействовать с виджетом).

Если у пользователя установлены несколько решений с виджетами, встроенными на одну страницу МоегоСклада, отображаются все виджеты. Порядок отображения соответствует расположению родительских решений в каталоге. В редакторах, поддерживающих функцию Drag-and-drop, пользователь может сам поменять порядок отображения виджетов.

Параметры содержимого виджетов:

- ширина `400px` — для виджетов всех решений,
- высота — статически указывается разработчиком в дескрипторе решения. В примере высота — `61px`.

Виджет можно скрыть, указав высоту `0px`. Скрытые виджеты не отображаются на страницах, но могут использовать все возможные протоколы, например [протокол **validation-feedback**](https://dev.moysklad.ru/doc/api/vendor/1.0/#validaciq-sostoqniq-redaktiruemogo-ob-ekta).
![[Pasted image 20260510130317.png]]

#### Кэширование виджетов
Система виджетов в МоемСкладе реализована так, чтобы, по-возможности, загружать виджет один раз. При первом открытии страницы с виджетом в рамках одной вкладки браузера происходит загрузка. Далее iframe c загруженным в него виджетом кэшируется и переиспользуется во всех последующих открытиях страницы с виджетом в рамках одной вкладки браузера.

Если по техническим причинам кэширование не произошло, хост-окно может:

- создать несколько iframe-экземпляров для одной точки расширения в рамках одной вкладки браузера (эти экземпляры могут существовать одновременно);
- не кэшировать iframe виджета после загрузки.

#### Протоколы виджетов

Для передачи данных между МоимСкладом и виджетами может использоваться один из перечисленных ниже протоколов обмена сообщениями через [postMessage](https://developer.mozilla.org/en-US/docs/Web/API/Window/postMessage).

Если виджет поддерживает [протокол **open-feedback**](https://dev.moysklad.ru/doc/api/vendor/1.0/#protokol-obratnoj-swqzi-pri-otkrytii-widzheta), система не отображает содержимое виджета сразу, а ждет ответного сообщения от виджета о готовности. До этого момента внутри виджета отображается заглушка. Когда виджет готов, он отправляет сообщение `OpenFeedback`. После этого система полностью открывает виджет пользователю. Виджеты без поддержки этого протокола отображаются сразу как только получают сообщение `Open`, даже если они к этому моменту еще не успели обновить отображаемую информацию.

Если виджет поддерживает [протокол **change-handler**](https://dev.moysklad.ru/doc/api/vendor/1.0/#poluchenie-sostoqniq-redaktiruemogo-ob-ekta), при редактировании документа пользователем на странице с виджетом он оповещается об изменениях, получая сообщение `Change`, содержащее несохраненное состояние документа.

Если виджет поддерживает [протокол **validation-feedback**](https://dev.moysklad.ru/doc/api/vendor/1.0/#validaciq-sostoqniq-redaktiruemogo-ob-ekta), то в ответ на сообщение `Change` он может запрещать хост-окну сохранять документ, если тот невалиден.

Если виджет поддерживает [протокол **update-provider**](https://dev.moysklad.ru/doc/api/vendor/1.0/#izmenenie-sostoqniq-redaktiruemogo-ob-ekta), при редактировании документа пользователем на странице с виджетом он может изменять несохраненное состояние документа, отправляя сообщение `UpdateRequest` со списком полей, которые необходимо изменить.

При сохранении страницы с виджетом, если виджет, который находится на экране редактирования сущности, поддерживает [протокол **save-handler**](https://dev.moysklad.ru/doc/api/vendor/1.0/#sohranenie-pol-zowatelem-redaktiruemogo-ob-ekta), он оповещается о факте сохранения объекта пользователем, получая сообщение `Save`.

Виджет, поддерживающий [протокол **dirty-state**](https://dev.moysklad.ru/doc/api/vendor/1.0/#priznak-nesohranennogo-sostoqniq-widzheta), может сообщить хост-окну, что в виджете есть несохраненные изменения. Для этого виджет отправляет хост-окну сообщение `SetDirty`. Виджет может отправить хост-окну сообщение `ClearDirty`, после чего диалог подтвержения закрытия окна не будет появляться, при условии, что отсутствуют несохраненные изменения в самом UI МоегоСклада или в других виджетах. Внутренний dirty-флаг для виджета в хост-окне сбрасывается при открытии. То есть при отправке сообщения `Open` хост-окно считает, что в виджете нет несохраненных изменений.

Поддержку виджетом протоколов **open-feedback**, **save-handler**, **dirty-state**, **change-handler** необходимо указать в [дескрипторе](https://dev.moysklad.ru/doc/api/vendor/1.0/#deskriptor-resheniq) решения. Каждая точка встраивания имеет свой [список поддерживаемых протоколов](https://dev.moysklad.ru/doc/api/vendor/1.0/#dostupnost-dopolnitel-nyh-protokolow-w-zawisimosti-ot-tochek-wstraiwaniq).

Виджеты на страницах создания, например в точке встраивания `document.customerorder.create`, имеют ограниченную функциональность по сравнению с виджетами на страницах редактирования, например `document.customerorder.edit`. Это выражается в меньшем количестве поддерживаемых протоколов и в реализации самих протоколов. Например, в сообщении **Change** часть полей, которые заполняются после первого сохранения документа (`id`, `created`, `meta` и другие) будет заполнено значением `null`.

Особенность работы документа Заказ кодов маркировки после его отправки в Честный Знак (через кнопку Заказ кодов):

- Документ доступен для редактирования, но сохранение изменений заблокировано. Т.е. на любые изменения в документе (статусы, поля) в виджет будут приходить сообщения **Change** без последующего **Save**.

Поддержка [сервисных протоколов](https://dev.moysklad.ru/doc/api/vendor/1.0/#blok-serwisnyh-protokolow-uses) виджетами на страницах создания пока не реализована.

#### Открытие виджета

Когда пользователь открывает страницу с виджетом, хост-окно отображает iframe виджета, только что загруженный или ранее закэшированный, и передает в этот iframe сообщение `Open` через [postMessage](https://developer.mozilla.org/en-US/docs/Web/API/Window/postMessage).

Пример сообщения `Open` cмотрите в правой части экрана.
Здесь:

- `extensionPoint` — текущая точка расширения;
- `objectId` — идентификатор текущего документа или сущности. Для виджета, отображаемого на экране создания, значение — `null`;
- `displayMode` — режим отображения виджета. Сейчас может принимать только одно значение `expanded`.

Виджет при получении сообщения `Open` может, например, обратиться на сервер за данными для указанного объекта `objectId` и отобразить их пользователю.

**Примечание**: в сообщении Open передается идентификатор текущей открытой сущности в карточке, который отображается в URL браузера в параметре `id`. Несмотря на то, что для сущностей Товар, Услуга, Комплект и Модификация этот идентификатор отличается от используемого в remap API, запрос по нему по-прежнему будет работать. При этом сервер будет использовать [редирект](https://developer.mozilla.org/ru/docs/Web/HTTP/Status/308) . Пример запроса для Товара `https://online.moysklad.ru/app/#good/edit?id=9e73d736-a0de-11e9-9109-f8fc00095c7f` приведен в правой части экрана. Для упрощения часть вывода пропущена.

> Сообщение Open для виджета на экране создания в Заказе покупателя

```
    {
      "name": "Open",
      "messageId": 12345,
      "extensionPoint": "document.customerorder.create",
      "objectId": null,
      "displayMode": "expanded"
    }
```
> Сообщение Open для виджета на экране редактирования в Заказе покупателя

```
    {
      "name": "Open",
      "messageId": 12345,
      "extensionPoint": "document.customerorder.edit",
      "objectId": "8e9512f3-111b-11ea-0a80-02a2000a3c9c",
      "displayMode": "expanded"
    }
```
> Ответ на запрос получения Товара

```
curl -X GET --location "https://api.moysklad.ru/api/remap/1.2/entity/product/9e73d736-a0de-11e9-9109-f8fc00095c7f"     -H "Content-Type: application/json"     -H "Authorization: Bearer ..." -v 

> GET /api/remap/1.2/entity/product/9e73d736-a0de-11e9-9109-f8fc00095c7f HTTP/1.1
> Host: online.moysklad.ru
> User-Agent: curl/7.68.0
> Accept: */*
> Content-Type: application/json
> Authorization: Bearer ...
> 
* Mark bundle as not supporting multiuse
< HTTP/1.1 308 Permanent Redirect
< Server: nginx/1.18.0
< Date: Fri, 28 Jan 2022 11:13:00 GMT
< Content-Length: 0
< Connection: keep-alive
< Cache-Control: max-age=0, no-cache
< X-Lognex-Reset: 0
< X-Lognex-Retry-After: 0
< Location: https://api.moysklad.ru/api/remap/1.2/entity/product/9e73e41d-a0de-11e9-9109-f8fc00095c81
< X-Lognex-Retry-TimeInterval: 3000
< X-RateLimit-Remaining: 44
< X-RateLimit-Limit: 45
< Strict-Transport-Security: max-age=15552000
< 
* Connection #1 to host online.moysklad.ru left intact
* Issue another request to this URL: 'https://api.moysklad.ru/api/remap/1.2/entity/product/9e73e41d-a0de-11e9-9109-f8fc00095c81'
* Found bundle for host online.moysklad.ru: 0x55cb04fa3970 [serially]
* Can not multiplex, even if we wanted to!
* Re-using existing connection! (#1) with host online.moysklad.ru
* Connected to online.moysklad.ru (88.212.252.4) port 443 (#1)
> GET /api/remap/1.2/entity/product/9e73e41d-a0de-11e9-9109-f8fc00095c81 HTTP/1.1
> Host: online.moysklad.ru
> User-Agent: curl/7.68.0
> Accept: */*
> Content-Type: application/json
> Authorization: Bearer ...
> 
* Mark bundle as not supporting multiuse
< HTTP/1.1 200 OK
< Server: nginx/1.18.0
< Date: Fri, 28 Jan 2022 11:13:00 GMT
< Content-Type: application/json;charset=utf-8
< Content-Length: 6535
< Connection: keep-alive
< Vary: Accept-Encoding
< Cache-Control: no-cache
< X-Lognex-Reset: 0
< X-Lognex-Retry-After: 0
< X-Lognex-Retry-TimeInterval: 3000
< X-RateLimit-Remaining: 43
< X-RateLimit-Limit: 45
< Strict-Transport-Security: max-age=15552000
< 
{
  "meta" : {
    "href" : "https://api.moysklad.ru/api/remap/1.2/entity/product/9e73e41d-a0de-11e9-9109-f8fc00095c81",
    "metadataHref" : "https://api.moysklad.ru/api/remap/1.2/entity/product/metadata",
    "type" : "product",
    "mediaType" : "application/json",
    "uuidHref" : "https://online.moysklad.ru/app/#good/edit?id=9e73d736-a0de-11e9-9109-f8fc00095c7f"
  },
  "id" : "9e73e41d-a0de-11e9-9109-f8fc00095c81",
  ...
}
```

#### Протокол обратной связи при открытии виджета

По умолчанию при открытии закэшированного виджета его содержимое отображается сразу.

Если виджет при открытии делает обращение к серверу, может быть видна небольшая задержка. В это время будет отображается прежнее состояние и содержание виджета, например, данные для прошлого контрагента.

Протокол обратной связи позволяет виджету явно сообщить хост-окну в какой именно момент отобразить содержимое виджета. До этого содержимое виджета будет закрыто ненавязчивым лоадером:

![[Pasted image 20260510130441.png]]

Для переключения хост-окна на использование протокола обратной связи при открытии виджета в дескрипторе для виджета надо явно указать поддержку дополнительного протокола **open-feedback**. Пример тега дополнительных протоколов supports с указанным в нем протоколом **open-feedback** смотрите в правой части экрана.

Виджет передает сообщение `OpenFeedback` хост-окну в качестве сигнала готовности содержимого виджета для отображения пользователю. Пример сообщения `OpenFeedback` смотрите в правой части экрана.
Здесь `correlationId` содержит значение `messageId` ранее полученного сообщения `Open`.

Хост-окно, получив сообщение `OpenFeedback`, отображает содержимое виджета пользователю и убирает лоадер.

> Тег дополнительных протоколов supports с протоколом open-feedback

```
    <supports>
        <open-feedback/>
    </supports>
```

> Cообщение OpenFeedback

```
{
  "name": "OpenFeedback",
  "correlationId": 12345
}
```

#### Сохранение пользователем редактируемого объекта

Хост-окно может оповещать виджет о факте сохранения редактируемого объекта. Для этого в дескрипторе для виджета нужно объявить поддержку опционального протокола **save-handler**.
Хост-окно отправляет виджету сообщение `Save` через [postMessage](https://developer.mozilla.org/en-US/docs/Web/API/Window/postMessage) при сохранении редактируемого объекта после сохранения объекта в базе данных. То есть на момент получения виджетом сообщения Save сохраненное состояние объекта уже доступно через JSON API.

Сохранение редактируемого объекта инициируется пользователем:

- при явном нажатии на кнопку **Сохранить**, в том числе при сохранении объекта без фактического внесения изменений;
- при покидании объекта и его явном сохранении через диалог подтверждения сохранения изменений, в том числе при листании кнопками-стрелочками на соседние объекты;
- при автоматическом сохранении изменений закрываемого объекта, например, при создании связанного документа Отгрузки из Заказа покупателя.
> Тег дополнительных протоколов supports с протоколом save-handler

```
      <supports>
          <save-handler/>
      </supports>
```
> Сообщение Save

```
     {
       "name": "Save",
       "messageId": 32109,
       "extensionPoint": "entity.counterparty.edit",
       "objectId": "8e9512f3-111b-11ea-0a80-02a2000a3c9c"
     }
```

Здесь:

- `extensionPoint` — текущая точка расширения;
- `objectId` — идентификатор текущего документа или сущности, аналогичен идентификатору в сообщении `Open`.
#### Признак несохраненного состояния виджета
Хост-окно поддерживает подтверждение закрытия окна пользователем, если он изменил данные в форме виджета, но не сохранил их. Для этого в дескрипторе для виджета нужно объявить поддержку опционального протокола **dirty-state**.
После того, как пользователь внес изменения в виджет, он отправляет хост-окну сообщение `SetDirty`. Пример сообщения SetDirty смотрите в правой части экрана.
Здесь openMessageId содержит значение messageId ранее полученного сообщения `Open`.
Система учитывает, что в виджете есть несохраненные изменения. Далее, если пользователь нажимает кнопку **Закрыть** или другим способом пытается уйти с формы редактирования, система отображает диалог подтверждения сохранения изменений:
> Тег дополнительных протоколов supports с протоколом dirty-state

```
  <supports>
      <dirty-state/>
  </supports>
```
> Сообщение SetDirty

```
    {
      "name": "SetDirty",
      "messageId": 12,
      "openMessageId": 7
    }
```

![[Pasted image 20260510130742.png]]

Если виджет после отправки `SetDirty` отправляет хост-окну сообщение `ClearDirty`, система не учитывает данный виджет при отображении диалога подтверждения сохранения изменений. То есть, если отсутствуют прочие несохраненные изменения самого объекта или в других виджетах, система не запрашивает диалог подтверждения сохранения изменений при закрытии редактируемого объекта.
> Сообщение ClearDirty

```
    {
      "name": "ClearDirty",
      "messageId": 13
    }
```

#### Получение состояния редактируемого объекта

Хост-окно может оповещать виджет об изменениях несохраненного состояния редактируемого объекта. Для этого в дескрипторе для виджета нужно объявить поддержку опционального протокола **change-handler**.
> Тег дополнительных протоколов supports с протоколом change-handler

```
      <supports>
          <change-handler/>
      </supports>
```
Хост-окно отправляет виджету сообщение `Change` через [postMessage](https://developer.mozilla.org/en-US/docs/Web/API/Window/postMessage), содержащее несохраненное состояние документа при редактировании документа пользователем.

Отправка сообщения `Change` инициируется при следующих действиях пользователя:

- при изменении полей документа, в том числе дополнительных полей, путем редактирования/выбора значения в селекторе;
- при добавлении/удалении/редактировании позиций документа.

Отправка сообщения `Change` **не происходит** в следующих случаях:

- при открытии экрана редактирования документа;
- при изменении состояния документа в результате сохранения пользователем;
- при изменении полей, которые не поддерживаются;
- если при редактировании значение редактируемого поля не изменилось, то есть при отсутствии реальных изменений.

Узнать, для каких точек поддерживается протокол **change-handler**, можно [тут](https://dev.moysklad.ru/doc/api/vendor/1.0/#dostupnost-dopolnitel-nyh-protokolow-w-zawisimosti-ot-tochek-wstraiwaniq).

Пример сообщения `Change` cмотрите в правой части экрана.

Здесь `changeHints` представляет собой массив с подсказками о том, что именно было изменено в редактируемом объекте:

- `_fields` — стандартные простые и ссылочные поля объекта (название, даты, контрагент и т. п.);  
    
- `positions` — позиции документа;
- `attributes` — значения дополнительных полей объекта.

Поле `objectState` — измененное состояние объекта, которое представляет собой JavaScript-объект, соответствующий по структуре ответу JSON API 1.2 на получение того же объекта (документа) с позициями.

Несмотря на то, что структура `objectState` в целом соответствует JSON API 1.2, имеются расхождения:

- Поля, обязательные в JSON API 1.2, могут быть не заданы в несохраненном состоянии документа. В качестве значение таких полей в `objectState` передается `null`.
- Числовые поля, которые могут иметь разные типы (целочисленные и с плавающей точкой) в JSON API 1.2, в `objectState` имеют один и тот же тип [Number](https://developer.mozilla.org/ru/docs/Glossary/Number). Это связано с тем, что `objectState` передается не как JSON, а как JavaScript Object.
- В `objectState` передается документ со всеми позициями, что в целом соответствует запросу в JSON API c `expand=positions`. При этом в метаданных позиций документа всегда `offset=0`, а `limit` зависит от `size`: `limit=1000`, если `size <= 1000` и `limit=size` если `size > 1000`.
- В objectState учитывается URL сервиса — [МойСклад](https://online.moysklad.ru/).
- В дополнительных полях типа Файл в `value` содержится имя файла с расширением, в отличие от JSON API 1.2.
- На страницах создания (точка расширения `*.create`) часть полей, которые заполняются после первого сохранения документа, могут быть не заполнены — иметь значение `null`:
    - `id`, `accountId`, `created`, `meta`, `href`, `uuidHref` для документа;
    - `externalCode` для документа, кроме Заказа покупателя, где внешний код может быть заполнен пользователем;
    - `id`, `accountId`, `meta`, `href`, `uuidHref` для позиций документа.
- на страницах создания некоторые поля могут иметь другое значение:
    - `updated` — заполняется временем открытия страницы документа.

Актуальные сведения о поддержке конкретных полей документов в протоколе **change-handler** смотрите в документации JSON API 1.2:

- [Заказ покупателя](https://dev.moysklad.ru/doc/api/remap/1.2/documents/#dokumenty-zakaz-pokupatelq)
- [Оприходование](https://dev.moysklad.ru/doc/api/remap/1.2/documents/#dokumenty-oprihodowanie)
- [Отгрузка](https://dev.moysklad.ru/doc/api/remap/1.2/documents/#dokumenty-otgruzka)
- [Приемка](https://dev.moysklad.ru/doc/api/remap/1.2/documents/#dokumenty-priemka)
- [Перемещение](https://dev.moysklad.ru/doc/api/remap/1.2/documents/#dokumenty-peremeschenie)
- [Списание](https://dev.moysklad.ru/doc/api/remap/1.2/documents/#dokumenty-spisanie)
- [Счет покупателю](https://dev.moysklad.ru/doc/api/remap/1.2/documents/#dokumenty-schet-pokupatelu)
- [Счет поставщика](https://dev.moysklad.ru/doc/api/remap/1.2/documents/#dokumenty-schet-postawschika)
- [Возврат покупателя](https://dev.moysklad.ru/doc/api/remap/1.2/documents/#dokumenty-vozwrat-pokupatelq)
- [Розничная продажа](https://dev.moysklad.ru/doc/api/remap/1.2/documents/#dokumenty-roznichnaq-prodazha)
- [Заказ кодов маркировки](https://dev.moysklad.ru/doc/api/remap/1.2/#/documents/emissionorder#2-zakaz-kodov-markirovki)
> Сообщение Change

```
{
  "name": "Change",
  "extensionPoint": "document.customerorder.edit",
  "messageId": 7,
  "changeHints": [
    "positions",
    "_fields"
  ],
  "objectState": {
    "meta": {
      "href": "https://api.moysklad.ru/api/remap/1.2/entity/customerorder/c4c6e6ea-b3f5-11eb-0a80-35ed000000b8",
      "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata",
      "type": "customerorder",
      "mediaType": "application/json",
      "uuidHref": "https://online.moysklad.ru/app/#customerorder/edit?id=c4c6e6ea-b3f5-11eb-0a80-35ed000000b8"
    },
    "id": "c4c6e6ea-b3f5-11eb-0a80-35ed000000b8",
    "accountId": "5fc956ad-b3f2-11eb-0a80-1b8a00000000",
    "created": "2021-05-13 17:16:11.465",
    "payedSum": 0,
    "shippedSum": 0,
    "invoicedSum": 0,
    "name": "00001",
    "applicable": true,
    "moment": "2021-05-13 17:15:00.000",
    "store": {
      "meta": {
        "href": "https://api.moysklad.ru/api/remap/1.2/entity/store/605491e4-b3f2-11eb-0a80-35ed00000074",
        "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/store/metadata",
        "type": "store",
        "mediaType": "application/json",
        "uuidHref": "https://online.moysklad.ru/app/#warehouse/edit?id=605491e4-b3f2-11eb-0a80-35ed00000074"
      }
    },
    "rate": {
      "currency": {
        "meta": {
          "href": "https://api.moysklad.ru/api/remap/1.2/entity/currency/6055a619-b3f2-11eb-0a80-35ed00000079",
          "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/currency/metadata",
          "type": "currency",
          "mediaType": "application/json",
          "uuidHref": "https://online.moysklad.ru/app/#currency/edit?id=6055a619-b3f2-11eb-0a80-35ed00000079"
        }
      }
    },
    "organization": {
      "meta": {
        "href": "https://api.moysklad.ru/api/remap/1.2/entity/organization/6051401c-b3f2-11eb-0a80-35ed00000072",
        "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/organization/metadata",
        "type": "organization",
        "mediaType": "application/json",
        "uuidHref": "https://online.moysklad.ru/app/#mycompany/edit?id=6051401c-b3f2-11eb-0a80-35ed00000072"
      }
    },
    "agent": {
      "meta": {
        "href": "https://api.moysklad.ru/api/remap/1.2/entity/counterparty/60550738-b3f2-11eb-0a80-35ed00000077",
        "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/counterparty/metadata",
        "type": "counterparty",
        "mediaType": "application/json",
        "uuidHref": "https://online.moysklad.ru/app/#company/edit?id=60550738-b3f2-11eb-0a80-35ed00000077"
      }
    },
    "state": {
      "meta": {
        "href": "https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/states/60850d6a-b3f2-11eb-0a80-35ed00000097",
        "type": "state",
        "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata",
        "mediaType": "application/json"
      }
    },
    "externalCode": "JAGi0Yg0i0OYvylp7SzDi3",
    "vatEnabled": true,
    "vatIncluded": true,
    "vatSum": 0,
    "sum": 21000,
    "updated": "2021-05-13 17:16:11.434",
    "reservedSum": 20000,
    "attributes": [
      {
        "meta": {
          "href": "https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/attributes/14fb3ad9-b3f6-11eb-0a80-35ed000000cb",
          "type": "attributemetadata",
          "mediaType": "application/json"
        },
        "id": "14fb3ad9-b3f6-11eb-0a80-35ed000000cb",
        "name": "Строка",
        "type": "string",
        "value": "123АААББвQ"
      },
      {
        "meta": {
          "href": "https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/attributes/14fbcb79-b3f6-11eb-0a80-35ed000000cc",
          "type": "attributemetadata",
          "mediaType": "application/json"
        },
        "id": "14fbcb79-b3f6-11eb-0a80-35ed000000cc",
        "name": "Ссылка",
        "type": "link",
        "value": null
      },
      {
        "meta": {
          "href": "https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/attributes/14fbd363-b3f6-11eb-0a80-35ed000000cd",
          "type": "attributemetadata",
          "mediaType": "application/json"
        },
        "id": "14fbd363-b3f6-11eb-0a80-35ed000000cd",
        "name": "Компания",
        "type": "counterparty",
        "value": {
          "meta": {
            "href": "https://api.moysklad.ru/api/remap/1.2/entity/counterparty/6054e7f9-b3f2-11eb-0a80-35ed00000075",
            "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/counterparty/metadata",
            "type": "counterparty",
            "mediaType": "application/json",
            "uuidHref": "https://online.moysklad.ru/app/#company/edit?id=6054e7f9-b3f2-11eb-0a80-35ed00000075"
          },
          "name": "ООО \"Поставщик\""
        }
      }
    ],
    "positions": {
      "meta": {
        "href": "https://api.moysklad.ru/api/remap/1.2/entity/customerorder/c4c6e6ea-b3f5-11eb-0a80-35ed000000b8/positions",
        "type": "customerorderposition",
        "mediaType": "application/json",
        "size": 2,
        "limit": 1000,
        "offset": 0
      },
      "rows": [
        {
          "meta": {
            "href": null,
            "type": "customerorderposition",
            "mediaType": "application/json"
          },
          "id": null,
          "accountId": "5fc956ad-b3f2-11eb-0a80-1b8a00000000",
          "price": 10000,
          "quantity": 2,
          "reserve": 2,
          "shipped": 0,
          "assortment": {
            "meta": {
              "href": "https://api.moysklad.ru/api/remap/1.2/entity/product/788a1cc7-b3f6-11eb-0a80-35ed000000e2",
              "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/product/metadata",
              "type": "product",
              "mediaType": "application/json",
              "uuidHref": "https://online.moysklad.ru/app/#good/edit?id=78896bd4-b3f6-11eb-0a80-35ed000000e0"
            }
          },
          "vat": 0,
          "discount": 0
        },
        {
          "meta": {
            "href": null,
            "type": "customerorderposition",
            "mediaType": "application/json"
          },
          "id": null,
          "accountId": "5fc956ad-b3f2-11eb-0a80-1b8a00000000",
          "price": 1000,
          "quantity": 1,
          "shipped": 0,
          "assortment": {
            "meta": {
              "href": "https://api.moysklad.ru/api/remap/1.2/entity/service/9d0c9a63-b3f6-11eb-0a80-35ed000000eb",
              "metadataHref": "https://api.moysklad.ru/api/remap/1.2/entity/service/metadata",
              "type": "service",
              "mediaType": "application/json",
              "uuidHref": "https://online.moysklad.ru/app/#good/edit?id=9d0c74c3-b3f6-11eb-0a80-35ed000000e9"
            }
          },
          "vat": 0,
          "discount": 0
        }
      ]
    }
  }
}
```

#### Валидация состояния редактируемого объекта

Виджет может проверять состояние редактируемого объекта и запрещать хост-окну сохранять объект, если он невалиден. Для этого в дескрипторе для виджета нужно объявить поддержку опционального протокола **validation-feedback**, который является параметром тега `change-handler`.
> Тег дополнительных протоколов supports с протоколом change-handler

```
      <supports>
        <change-handler>
          <validation-feedback/>
        </change-handler>
      </supports>
```

Протокол работает в паре с `change-handler`, то есть виджет, поддерживающий протокол `validation-feedback`, должен отправить сообщение `ValidationFeedback` о валидности документа в ответ на сообщение `Change`.

Если виджет в сообщении `ValidationFeedback` укажет, что документ невалиден, то при попытке сохранить документ пользователь увидит сообщение об ошибке, которое включает в себя наименование виджета.

![[Pasted image 20260510130859.png]]

Если виджет по какой-то причине не отправит `ValidationFeedback` или отправит некорректное сообщение, пользователь не сможет сохранить документ.

Подробнее о том, для каких точек поддерживается протокол **validation-feedback**, смотрите [здесь](https://dev.moysklad.ru/doc/api/vendor/1.0/#dostupnost-dopolnitel-nyh-protokolow-w-zawisimosti-ot-tochek-wstraiwaniq).

Пример сообщений `ValidationFeedback` cмотрите в правой части экрана.

Здесь:

- `messageId` — целочисленный идентификатор сообщения, уникальный в рамках текущего взаимодействия виджет — хост-окно. Назначается виджетом;
- `correlationId` — идентификатор соответствующего сообщения `Change`;
- `valid` — признак валидности документа;
- `message` — сообщение об ошибке. Требуется для случая когда `valid=false`. Максимум 100 символов.
> Сообщение ValidationFeedback — документ валиден (может быть сохранен)

```
{
  "name": "ValidationFeedback",
  "messageId": 11,
  "correlationId": 10,
  "valid": true
}
```

> Сообщение ValidationFeedback — документ невалиден (не должен быть сохранен)

```
{
  "name": "ValidationFeedback",
  "messageId": 12,
  "correlationId": 11,
  "valid": false,
  "message": "Пример ошибки от разработчика"
}
```

#### Изменение состояния редактируемого объекта
> Тег дополнительных протоколов supports с протоколом update-provider

```
      <supports>
          <update-provider/>
      </supports>
```
Виджет может изменять поля текущего редактируемого объекта посредством передачи сообщения `UpdateRequest` хост-окну. Для этого в дескрипторе для виджета нужно объявить поддержку опционального протокола **update-provider**.

Изменения в этом протоколе, в отличие от JSON API, происходят без сохранения состояния объекта в базе данных МоегоСклада, так же, как если бы они были сделаны самим пользователем. Виджет должен отправлять сообщения `UpdateRequest` преимущественно в качестве реакции на действия пользователя, чтобы предупредить его об изменениях в редактируемом документе.
> Сообщение UpdateRequest

```
{
  "name": "UpdateRequest",
  "messageId": 10,
  "updateState": {
    "name": "1",
    "deliveryPlannedMoment": "2021-08-21T12:15:50.333Z",
    "applicable": true,
    "description": null
  }
}
```

Сценарий работы:

1. Виджет отправляет хост-окну сообщение `UpdateRequest`, содержащее набор полей документа и/или позиции документа, которые нужно изменить.
2. Хост-окно валидирует содержимое сообщения и отправляет обратно ответ `UpdateRequest`.
3. Если сообщение `UpdateRequest` невалидно, хост-окно отправляет в ответ сообщение `InvalidMessageError`, содержащее описание ошибочных полей.

Примеры сообщений `UpdateRequest` смотрите в правой части экрана.

Здесь:

- `messageId` — целочисленный идентификатор сообщения, уникальный в рамках текущего взаимодействия виджет — хост-окно. Назначается виджетом;
- `updateState` — список полей, которые необходимо изменить. Соответствует телу запроса для обновления соответствующего документа в JSON API, смотрите, например, [Заказ покупателя](https://dev.moysklad.ru/doc/api/remap/1.2/documents/#dokumenty-zakaz-pokupatelq-izmenit-zakaz-pokupatelq).
Пример сообщения `UpdateResponse`
> Сообщение UpdateResponse

```
{
  "name": "UpdateResponse",
  "correlationId": 10
}
```

Здесь:

- `correlationId` — идентификатор соответствующего сообщения `UpdateRequest`.

> Сообщение UpdateRequest для изменения дополнительных полей

```
{
  "name":"UpdateRequest",
  "messageId":10,
  "updateState":{
    "description": "красивое",
    "attributes":[
      {
        "meta":{
          "href":"https://api.moysklad.ru/api/remap/1.2/entity/customerorder/metadata/attributes/f6d39a2b-146f-11ec-0a80-072a002cf678",
          "type":"attributemetadata"
        },
        "name":"Доставлено в срок",
        "type":"boolean",
        "value":true
      },
      {
        "id":"f6d39d12-146f-11ec-0a80-072a002cf679",
        "name":"Срок доставки, дней",
        "type":"long",
        "value":10
      },
      {
        "id":"f6d39d12-146f-11ec-0a80-072a002cf678",
        "value":45.78
      }
    ]
  }
}
```

**Работа с полями из `updateState`**:

- Список может содержать одно или несколько полей для изменения.
- При изменении значения поля на то же самое, поле в интерфейсе не обновляется и документ не считается измененным. То есть пользователь может закрыть экран редактирования документа без диалога с вопросом «Данные были изменены. Сохранить изменения?». К позициям это не относится: если позиции в запросе есть, список всегда обновляется и документ считается измененным.
- Содержимое поля можно сбросить, указав в качестве его значения `null`.
- Если пришло несколько сообщений подряд, все они обрабатываются последовательно.
- Поля типа **Дата-время** необходимо передавать с включением информации о часовом поясе, чтобы избежать неопределенности в интерпретации.
- Значения полей типа **Дата-время** всегда округляются до минут, секунды отбрасываются.
- Для значений ссылочных полей обязательными являются `meta.href` и `meta.type`, остальные поля внутри `meta` игнорируются.
- Для одновременного изменения согласованных полей, таких как Организация (Контрагент), Счет, Договор, необходимо, чтобы их значения были совместимы. Счет должен принадлежать указанной организации, договор должен относиться к этой организации и контрагенту. Иначе возникнет ошибка валидации и значения полей в интерфейсе не изменятся.

**Работа с дополнительными полями (attributes)**:

- Для идентификации дополнительного поля необходимо указать `meta.href` либо `id`. Если указаны оба поля, значение берется из `meta.href`.
- Для передачи значения поля служит поле `value`.
- Остальные поля не являются обязательными.
- Пока не поддерживаются дополнительные поля типа Файл.

**Работа с позициями (positions)**:

- При указании позиций в сообщении `UpdateRequest` существующие позиции в документе полностью заменяются позициями из сообщения.
- Для редактирования позиции необходимо использовать `id` существующей позиции, например, получив их в сообщении `Change` или через JSON API.
- При необходимости добавить новые позиции с сохранением существующих можно указать `id` существующих позиций и новые позиции. В результате останутся существующие позиции и добавятся новые.
- До сохранения документа в базе данных у новых позиций отсутствует `id`.
- Позиции добавляются в порядке, который указан в сообщении.

Подробнее о том, для каких точек поддерживается протокол **update-provider**, смотрите в [статье](https://dev.moysklad.ru/doc/api/vendor/1.0/#dostupnost-dopolnitel-nyh-protokolow-w-zawisimosti-ot-tochek-wstraiwaniq).
> Сообщение UpdateRequest для добавления позиций

```
{
  "name":"UpdateRequest",
  "messageId":10,
  "updateState":{
    "vatIncluded": true,
    "positions": [
      {
        "quantity": 10,
        "price": 100,
        "discount": 0,
        "vat": 0,
        "assortment": {
          "meta": {
            "href": "https://api.moysklad.ru/api/remap/1.2/entity/product/8b382799-f7d2-11e5-8a84-bae5000003a5",
            "type": "product"
          }
        },
        "reserve": 10
      },
      {
        "quantity": 1,
        "price": 200,
        "assortment": {
          "meta": {
            "href": "https://api.moysklad.ru/api/remap/1.2/entity/service/be903062-f504-11e5-8a84-bae50000019a",
            "type": "service"
          }
        },
        "pack": null
      },
      {
        "quantity": 30,
        "price": 300,
        "discount": 0,
        "vat": 18,
        "assortment": {
          "meta": {
            "href": "https://api.moysklad.ru/api/remap/1.2/entity/bundle/c02e3a5c-007e-11e6-9464-e4de00000006",
            "type": "bundle"
          }
        },
        "pack": {
          "id": "1bf22e62-8b47-11e8-56c0-000800000006"
        },
        "reserve": 30
      }
    ]
  }
}
```
> Сообщение UpdateRequest для изменения существующих позиций

```
{
  "name":"UpdateRequest",
  "messageId":10,
  "updateState":{
    "vatIncluded": true,
    "positions": [
      {
        "id": "be903062-f504-11e5-8a84-bae50000019a",
        "price": 100,
        "discount": -10
      },
      {
        "id": "be903062-f504-11e5-8a84-bae500000123",
        "quantity": 30,
        "price": 300,
        "discount": 0,
        "vat": 18,
        "assortment": {
          "meta": {
            "href": "https://api.moysklad.ru/api/remap/1.2/entity/bundle/c02e3a5c-007e-11e6-9464-e4de00000006",
            "type": "bundle"
          }
        },
        "reserve": 30
      }
    ]
  }
}
```
> Сообщение UpdateRequest для добавления одной новой и сохранения трех существующих позиций

```
{
  "name":"UpdateRequest",
  "messageId":10,
  "updateState":{
    "vatIncluded": true,
    "positions": [
      {
        "id": "be903062-f504-11e5-8a84-bae50000019a"
      },
      {
        "id": "0fb51a51-e01d-48da-9035-4b21f5e69055"
      },
      {
        "id": "ef34072d-5fd3-4ac4-b4b9-87458ca61da2"
      },
      {
        "quantity": 1,
        "price": 300,
        "assortment": {
          "meta": {
            "href": "https://api.moysklad.ru/api/remap/1.2/entity/service/c02e3a5c-007e-11e6-9464-e4de00000006",
            "type": "service"
          }
        }
      }
    ]
  }
}
```
### Кастомные модальные окна
> Дескриптор с виджетом и главным iframe, использующие кастомные модальные окна

```
<ServerApplication  xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"             
                    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"             
                    xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2      
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
    <iframe>
        <sourceUrl>https://example.com/iframe.html</sourceUrl>
        <expand>true</expand>
    </iframe>
    <vendorApi>
        <endpointBase>https://example.com/dummy-app</endpointBase>
    </vendorApi>
    <access>
        <resource>https://api.moysklad.ru/api/remap/1.2</resource>
        <scope>admin</scope>
    </access>
    <widgets>        
        <entity.counterparty.edit>            
            <sourceUrl>https://example.com/widget.php</sourceUrl>            
            <height>                
                <fixed>150px</fixed>            
            </height>
            <uses>
                <good-folder-selector/>
            </uses>                  
        </entity.counterparty.edit>    
    </widgets>
    <popups>
        <popup>
            <name>somePopup</name>
            <sourceUrl>https://example.com/popup.php</sourceUrl>
        </popup>
        <popup>
            <name>somePopup2</name>
            <sourceUrl>https://example.com/popup-2.php</sourceUrl>
        </popup>
    </popups>
</ServerApplication>
```

> Сообщение ShowPopupRequest

```
{
  "name": "ShowPopupRequest",
  "messageId": 12,
  "popupName": "somePopup",
  "popupParameters": "hello"
}
```

> Сообщение OpenPopup

```
{
  "name": "OpenPopup",
  "messageId": 36,
  "popupName": "somePopup",
  "popupParameters": "hello"
}
```

> Сообщение ClosePopup

```
{
  "name": "ClosePopup",
  "messageId": 17,
  "popupResponse": "world"
}
```

> Сообщение ShowPopupResponse

```
{
  "name": "ShowPopupResponse",
  "correlationId": 12,
  "popupName": "somePopup",
  "popupResolution": "normal",
  "popupResponse": "world"
}
```

Модальные окна позволяют расширить функциональность главного окна решения, виджетов или кастомных кнопок.

Характеристики кастомного модального окна:

- Открывается на весь экран аналогично прочим модальным окнам в интерфейсе МоегоСклада. Например, вызываемое через иконку «карандаш» окно редактирования сущностей в полях.
- Является модальным, то есть открывается поверх текущей страницы МоегоСклада, и требует действия от пользователя внутри этого окна — взаимодействия с веб-страницей и/или закрытие окна.
- Содержимое окна определяет разработчик. Передавать данные можно от виджета (главного iframe) в модальное окно и в обратном направлении.
- Может получать текущий [контекст пользователя](https://dev.moysklad.ru/doc/api/vendor/1.0/#kontext-pol-zowatelq), так же как виджеты и главное окно решения.
- В качестве заголовка окна всегда используется название решения.
- Имеет кнопку принудительного закрытия для пользователя — «крестик» справа вверху.
- Изменяет свои размеры при изменении размеров окна браузера.

Рассмотрим работу кастомных модальных окон на примере виджетов. В случае главного iframe все работает аналогично. Отличия при вызове окна из кастомных кнопок будут описаны ниже.

Чтобы решение могло использовать кастомные модальные окна, добавьте в [дескриптор решения](https://dev.moysklad.ru/doc/api/vendor/1.0/#blok-popups) блок `<popups>...</popups>`. Пример дескриптора смотрите в правой части экрана.

Виджет может отобразить одно из кастомных модальных окон, отправив сообщение `ShowPopupRequest` с именем выбранного окна хост-окну. Пример такого сообщения смотрите в правой части экрана.

Здесь:

- `messageId` — идентификатор сообщения;
- `popupName` — имя открываемого окна;
- `popupParameters` — опциональные параметры, передаваемые окну виджетом. Может иметь любой тип, в том числе `null`.

МойСклад проверяет сообщение `ShowPopupRequest`. Если сообщение валидно, отображается модальное окно: загружается страница окна по адресу `sourceUrl` в iframe, в GET-параметре передается `contextKey`. Процесс аналогичен загрузке виджета. Значение `sourceUrl` загружается из соответствующего элемента списка модальных окон `<popups>` в дескрипторе. Поиск производится по `popupName`, переданному в сообщении.

После загрузки модального окна хост-окно отправляет ему сообщение `OpenPopup`. Набор полей тот же, что и в `ShowPopupRequest`. При этом `messageId` в данном сообщении свой, а не тот, что был передан в сообщении `ShowPopupRequest`.

Для закрытия модального окна, оно отправляет сообщение `ClosePopup` хост-окну. Пример такого сообщения смотрите в правой части экрана.

Здесь:

- `messageId` — идентификатор сообщения;
- `popupResponse` — опциональный ответ, возвращаемый виджету. Может иметь любой тип, в том числе `null`.

МойСклад, в свою очередь, отправляет сообщение `ShowPopupResponse` виджету, открывшему окно. Пример такого сообщения смотрите в правой части экрана.

Здесь:

- `correlationId` — идентификатор соответствующего сообщения ShowPopupRequest;
- `popupName` — имя открывавшегося модального окна;
- `popupResolution` — вариант, по которому произошло закрытие модального окна: `normal` — нормальное закрытие окна по `ClosePopup`, `closedByUser` — закрытие окна пользователем путем нажатия на «крестик»;
- `popupResponse` — опциональный ответ, возвращаемый виджету.

Страницы кастомных модальных окон кэшируются аналогично кэшированию виджетов. При повторном открытии окна по сообщению `ShowPopupRequest` переиспользуется ранее загруженный iframe.

Выше приводятся примеры работы с кастомными модальными окнами.
#### Пример работы без возврата параметров из модального окна

> Пример взаимодействия без передачи дополнительных параметров

```
// виджет -> хост-окно
{
  "name": "ShowPopupRequest",
  "messageId": 12,
  "popupName": "somePopup"
}

// хост-окно -> модальное окно
{
  "name": "OpenPopup",
  "messageId": 35,
  "popupName": "somePopup"
}

// хост-окно -> виджет
{
  "name": "ShowPopupResponse",
  "correlationId": 12,
  "popupName": "somePopup",
  "popupResolution": "closedByUser"
}
```

> Пример взаимодействия с передачей параметров в виде строки

```
// виджет -> хост-окно
{
  "name": "ShowPopupRequest",
  "messageId": 17,
  "popupName": "somePopup",
  "popupParameters": "hello"
}

// хост-окно -> модальное окно
{
  "name": "OpenPopup",
  "messageId": 36,
  "popupName": "somePopup",
  "popupParameters": "hello"
}

// хост-окно -> виджет
{
  "name": "ShowPopupResponse",
  "correlationId": 17,
  "popupName": "somePopup",
  "popupResolution": "closedByUser"
}
```

1. Виджет отправляет хост-окну сообщение `ShowPopupRequest`. В сообщении указывается имя модального окна и опциональные параметры.
2. Хост-окно отображает модальное окно, загружая страницу окна по адресу `sourceUrl` в iframe с передачей `contextKey` в GET-параметре.
3. Хост-окно отправляет в iframe модального окна сообщение `OpenPopup`, передавая в нем опциональные параметры от виджета.
4. Пользователь взаимодействует с веб-содержимым модального окна, после чего закрывает его через системную кнопку («крестик»), находящуюся в верхнем правом углу окна.
5. Система скрывает модальное окно и отправляет виджету сообщение `ShowPopupResponse` с указанием того, что окно было закрыто пользователем через системную кнопку (`"popupResolution": "closedByUser"`).

Пример модального окна с наличием только системной кнопки закрытия:

![useful image](https://dev.moysklad.ru/doc/api/vendor/1.0/images/popup-view.png)

> Закрытие модального окна с использованием сообщения ClosePopup

```
...
// модальное окно -> хост-окно
{
  "name": "ClosePopup",
  "messageId": 37,
}

// хост-окно -> виджет
{
  "name": "ShowPopupResponse",
  "correlationId": 14,
  "popupName": "somePopup",
  "popupResolution": "normal"
}
```
Разработчик может отобразить на странице и собственную кнопку закрытия окна. При нажатии на нее будет отправляться сообщение `ClosePopup`, а виджет получит сообщение `ShowPopupResponse` с `"popupResolution": "normal"`.

![useful image](https://dev.moysklad.ru/doc/api/vendor/1.0/images/popup-view-button.png)

#### Пример работы с возвратом параметров из модального окна

> Пример ответа с передачей данных о нажатой кнопке

```
// виджет -> хост-окно
{
  "name": "ShowPopupRequest",
  "messageId": 29,
  "popupName": "somePopup"
}

// хост-окно -> модальное окно
{
  "name": "OpenPopup",
  "messageId": 36,
  "popupName": "somePopup"
}

// пользователь нажимает на кнопку «Сохранить»

// модальное окно -> хост-окно
{
  "name": "ClosePopup",
  "messageId": 44,
  "popupResponse": "save"
}

// хост-окно -> виджет
{
  "name": "ShowPopupResponse",
  "correlationId": 29,
  "popupName": "somePopup",
  "popupResolution": "normal",
  "popupResponse": "save"
}
```

Если модальному окну требуется вернуть информацию обратно в виджет, окно должно передать ее в поле `popupResponse` сообщения `ClosePopup`.

1. Виджет отправляет хост-окну сообщение `ShowPopupRequest`, указывая в нем имя модального окна и опциональные параметры.
2. Хост-окно отображает модальное окно, загружая его страницу по адресу `sourceUrl` в iframe с передачей `contextKey` в GET-параметре.
3. Хост-окно отправляет в iframe модального окна сообщение `OpenPopup` с опциональными параметрами.
4. Пользователь взаимодействует с веб-содержимым модального окна, после чего нажимает кнопку закрытия или сохранения, находящуюся внутри страницы модального окна.
5. Модальное окно отправляет хост-окну сообщение `ClosePopup`. В нем передаются параметры, которые зависят от действий пользователя, например тип нажатой кнопки.
6. Система скрывает модальное окно и отправляет виджету сообщение `ShowPopupResponse` с указанием параметров, переданных модальным окном.

Пользователь может закрыть модальное окно принудительно. При этом параметры в виджет не будут переданы.

Пример модального окна с кнопками «Сохранить» и «Отмена»:

![useful image](https://dev.moysklad.ru/doc/api/vendor/1.0/images/popup-edit.png)

#### Отображение содержимого, которое не вмещается в окно целиком

> Пример плавающей верстки содержимого

```
<!doctype html>
<html lang="ru">
<head>
    <meta charset="utf-8">

    <title>Popup example</title>
    <style>
        body {
            overflow: hidden;
        }
        .main-container {
            display: flex;
            flex-direction: column;
            height: 100vh;
        }
        .content-container {
            overflow: auto;
            flex-grow: 1;
        }
        .buttons-container {
            padding-top: 15px;
            min-height: 55px;
        }
    </style>
    <link rel="stylesheet" href="css/uikit.css">
</head>

<body>
<div class="main-container">
    <div class="content-container">
        <!--Разместите содержимое здесь -->
    </div>
    <div class="buttons-container">
        <button class="button button--success">Сохранить</button>
        <button class="button">Отмена</button>
    </div>
</div>
</body>
</html>
```

Если во модальном окне нужно отобразить содержимое, которое может не поместиться на экране пользователя, используйте плавающую верстку. Так вы можете создать полосы прокрутки для содержимого, и кнопки закрытия окна всегда будут отображаться в нижней части окна.

Пример модального окна с полосами прокрутки:

![useful image](https://dev.moysklad.ru/doc/api/vendor/1.0/images/popup-scroll.png)

Пример такой верстки с использованием [UI Kit](https://github.com/moysklad/html-marketplace-1.0-uikit) представлен справа.

#### Способы передачи параметров

> Пример взаимодействия с передачей параметров в виде строки

```
// виджет -> хост-окно
{
  "name": "ShowPopupRequest",
  "messageId": 12,
  "popupName": "somePopup",
  "popupParameters": "hello"
}

// хост-окно -> модальное окно
{
  "name": "OpenPopup",
  "messageId": 35,
  "popupName": "somePopup",
  "popupParameters": "hello"
}
```

> Пример взаимодействия с передачей параметров в виде объекта

```
// виджет -> хост-окно
{
  "name": "ShowPopupRequest",
  "messageId": 12,
  "popupName": "somePopup",
  "popupParameters": {
    "aaa": 1,
    "bbb": "qwerty"
  }
}

// хост-окно -> модальное окно
{
  "name": "OpenPopup",
  "messageId": 35,
  "popupName": "somePopup",
  "popupParameters": {
    "aaa": 1,
    "bbb": "qwerty"
  }
}
```

> Пример взаимодействия с передачей параметров в виде массива

```
// виджет -> хост-окно
{
  "name": "ShowPopupRequest",
  "messageId": 12,
  "popupName": "somePopup",
  "popupParameters": [123, "foobar"]
}

// хост-окно -> модальное окно
{
  "name": "OpenPopup",
  "messageId": 35,
  "popupName": "somePopup",
  "popupParameters": [123, "foobar"]
}
```

Существует несколько способов передачи параметров между виджетами и модальными окнами:

- передача в виде примитивного значения;
- передача в виде объекта;
- передача в виде массива, в том числе массива объектов;
- передача в виде значения `null`.

Справа приведены примеры передачи параметров из виджета в модальное окно через сообщение `ShowPopupRequest`.

Аналогичные способы передачи можно использовать для возврата ответа в сообщении `ClosePopup`.

#### Вызов модального окна из кастомной кнопки

Кнопка может отобразить одно из модальных окон решения, отправив в ответе на [запрос обработки нажатия на кнопку](https://dev.moysklad.ru/doc/api/vendor/1.0/#obrabotka-nazhatiq-na-kastomnuu-knopku) значение `action=ShowPopup` с именем выбранного окна и опциональными параметрами.

Если такое окно описано в дескрипторе решения, то, как и для вызова через сообщение, оно загружается (с передачей `contextKey`) или берется из кэша. После загрузки модального окна хост-окно отправляет ему сообщение `OpenPopup` и передает опциональные параметры, полученные в запросе.

По окончании работы с модальным окном пользователь может закрыть его через системную кнопку либо само окно может отправить сообщение `ClosePopup`.

Отличия при работе с модальными окнами, вызванными по нажатию кастомной кнопки:

- нет взаимодействия с хост-окном (не используются сообщения `ShowPopupRequest` и `ShowPopupResponse`)

### Контекст пользователя

При первом открытии страницы с окном или виджетом (в рамках одной вкладки браузера) МойСклад загружает его содержимое GET-запросом по адресу, указанному в теге sourceUrl. При этом хост-окно добавляет в качестве параметров запроса следующие данные:

- **contextKey** — временный ключ, который может быть использован для получения данных пользователя через [Vendor API](https://dev.moysklad.ru/doc/api/vendor/1.0/#poluchenie-kontexta-pol-zowatelq). Время жизни этого ключа: 5 минут с момента генерации.
- **appUid** — appUid решения. Может быть использован для определения того, какое решение сейчас отображается у пользователя, если на одном и том же sourceUrl находятся несколько решений (например _dev_ и _prod_-версия).
- **appId** — ИД решения. Как и `appUid` может использоваться для определения того, какое решение сейчас отображается у пользователя.
- **userLocale** — идентификатор локали в формате, совместимом с POSIX. Определяет текущий язык, выбранный пользователем в настройках МоегоСклада. Может использоваться для выбора локализованных ресурсов (строк, форматов дат, чисел и т.п.). Поддерживаемые значения:
    - `ru_RU` - русский
    - `en_US` - английский.

Пример загружаемого URL для решения Онлайн-заказ (при условии, что `iframe.sourceUrl` в его дескрипторе имеет значение `https://example.com/iframe.html`):

`https://example.com/iframe.html?contextKey=1c14e98cd272239c03bf3d9697f167699743292c&appUid=online-order.moysklad&appId=f0e50ffd-e267-46bf-a963-0adcf2fe09e0&userLocale=ru_RU`.

### Сервисы хост-окна

В виджетах, iframe и модальных окнах доступны следующие сервисные возможности МоегоСклада (хост-окна):

- [Селектор группы товаров](https://dev.moysklad.ru/doc/api/vendor/1.0/#selektor-gruppy-towarow),
- [Стандартные диалоги](https://dev.moysklad.ru/doc/api/vendor/1.0/#standartnye-dialogi),
- [Протокол навигации](https://dev.moysklad.ru/doc/api/vendor/1.0/#protokol-nawigacii).

#### Селектор группы товаров

> Дескриптор решения с виджетом, использующим селектор группы товаров

```
<ServerApplication  xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"             
                    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"             
                    xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2      
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
    <iframe>
        <sourceUrl>https://example.com/iframe.html</sourceUrl>
        <expand>true</expand>
    </iframe>
    <vendorApi>
        <endpointBase>https://example.com/dummy-app</endpointBase>
    </vendorApi>
    <access>
        <resource>https://api.moysklad.ru/api/remap/1.2</resource>
        <scope>admin</scope>
    </access>
    <widgets>        
        <entity.counterparty.edit>            
            <sourceUrl>https://example.com/widget.php</sourceUrl>            
            <height>                
                <fixed>150px</fixed>            
            </height>
            <uses>
                <good-folder-selector/>
            </uses>                  
        </entity.counterparty.edit>    
    </widgets>
</ServerApplication>
```

> Дескриптор решения, главный iframe и модальное окно которого используют селектор группы товаров

```
<ServerApplication xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2      
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
  <iframe>
    <sourceUrl>https://example.com/iframe.html</sourceUrl>
    <expand>true</expand>
    <uses>
      <good-folder-selector/>
    </uses>
  </iframe>
  <vendorApi>
    <endpointBase>https://example.com/dummy-app</endpointBase>
  </vendorApi>
  <access>
    <resource>https://api.moysklad.ru/api/remap/1.2</resource>
    <scope>admin</scope>
  </access>
  <popups>
    <popup>
      <name>coolPopup</name>
      <sourceUrl>https://vendorurl.coolpopup.ru</sourceUrl>
      <uses>
        <good-folder-selector/>
      </uses>
    </popup>
  </popups>
</ServerApplication>
```

Позволяет виджетам, главному и модальным окнам решений переиспользовать существующий в МоемСкладе селектор группы товаров с получением ими результата выбора пользователя.

Чтобы виджет, главное или модальное окно начали поддерживать селектор в дескрипторе, необходимо добавить в блок `uses` для `widgets`, `iframe` или `popup` тег: `<good-folder-selector/>`.

Рассмотрим пример с виджетом. Когда виджет отправляет хост-окну сообщение `SelectGoodFolderRequest` через Window.postMessage, хост-окно запрашивает у пользователя выбор группы товаров, используя встроенный в МойСклад селектор:

![useful image](https://dev.moysklad.ru/doc/api/vendor/1.0/images/good-folder-selector.png)

> Cообщение SelectGoodFolderRequest

```
{
  "name": "SelectGoodFolderRequest",
  "messageId": 12345
}
```

Здесь: - `messageId` — целочисленный идентификатор сообщения, уникальный в рамках текущего взаимодействия виджет — хост-окно. Назначается виджетом.

После совершения пользователем выбора группы товаров или отказа от него хост-окно передает виджету результат действий пользователя в сообщении `SelectGoodFolderResponse`.

> Cообщение SelectGoodFolderResponse (Пользователь выбрал группу товаров, имеющую идентификатор 8e9512f3-111b-11ea-0a80-02a2000a3c9c)

```
{
  "name": "SelectGoodFolderResponse",
  "correlationId": 12345,
  "selected": true,
  "goodFolderId": "8e9512f3-111b-11ea-0a80-02a2000a3c9c"
}
```

Здесь:

- `correlationId` — идентификатор соответствующего сообщения `SelectGoodFolderRequest`;
- `selected` — признак наличия выбора;
- `goodFolderId` — идентификатор выбранной группы товаров.

> Cообщение SelectGoodFolderResponse (Пользователь отменил выбор)

```
{
  "name": "SelectGoodFolderResponse",
  "correlationId": 12345,
  "selected": false
}
```

#### Стандартные диалоги

> Дескриптор с виджетом, использующим стандартные диалоги

```
<ServerApplication  xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"             
                    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"             
                    xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2      
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
    <iframe>
        <sourceUrl>https://example.com/iframe.html</sourceUrl>
        <expand>true</expand>
    </iframe>
    <vendorApi>
        <endpointBase>https://example.com/dummy-app</endpointBase>
    </vendorApi>
    <access>
        <resource>https://api.moysklad.ru/api/remap/1.2</resource>
        <scope>admin</scope>
    </access>
    <widgets>        
        <entity.counterparty.edit>            
            <sourceUrl>https://example.com/widget.php</sourceUrl>            
            <height>                
                <fixed>150px</fixed>            
            </height>
            <uses>
                <standard-dialogs/>
            </uses>                  
        </entity.counterparty.edit>    
    </widgets>
</ServerApplication>
```

> Дескриптор решения, главное и модальное окно которого используют стандартные диалоги

```
<ServerApplication xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2      
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
  <iframe>
    <sourceUrl>https://example.com/iframe.html</sourceUrl>
    <expand>true</expand>
    <uses>
        <standard-dialogs/>
    </uses>
  </iframe>
  <vendorApi>
    <endpointBase>https://example.com/dummy-app</endpointBase>
  </vendorApi>
  <access>
    <resource>https://api.moysklad.ru/api/remap/1.2</resource>
    <scope>admin</scope>
  </access>
  <popups>
    <popup>
      <name>coolPopup</name>
      <sourceUrl>https://vendorurl.coolpopup.ru</sourceUrl>
      <uses>
          <standard-dialogs/>
      </uses>
    </popup>
  </popups>
</ServerApplication>
```

Позволяет виджетам, главному и кастомным модальным окнам использовать существующие в МоемСкладе стандартные диалоги.

Чтобы виджет, iframe или модальное окно начали поддерживать протокол, необходимо добавить в блок `uses` для `widgets`, `iframe` или `popup` тег: `<standard-dialogs/>`.

Рассмотрим пример с виджетом. Когда виджет хочет показать пользователю стандартный диалог, он отправляет хост-окну сообщение `ShowDialogRequest`. В сообщении указывается текст сообщения и кнопки, которые необходимо отобразить пользователю. Наример:

![useful image](https://dev.moysklad.ru/doc/api/vendor/1.0/images/standard-dialog-with-two-buttons.png)

> Cообщение ShowDialogRequest

```
{
  "name": "ShowDialogRequest",
  "messageId": 12345,
  "dialogText": "Учетная запись будет удалена. Вы хотите продолжить?",
  "buttons": [
    {"name": "Yes", "caption": "Да, удалить"},
    {"name": "No", "caption": "Нет"}
  ]
}
```

Параметры сообщения `ShowDialogRequest`:

- `messageId` — целочисленный идентификатор сообщения, уникальный в рамках текущего взаимодействия виджет — хост-окно. Назначается виджетом;
- `dialogText` — текст сообщения, который нужно отобразить пользователю МоегоСклада. Максимальный размер — 4096 символов. HTML-теги не допускаются (будут экранированы);
- `buttons` — список кнопок в диалоге, элементами которого являются объекты с двумя обязательными полями: `name` — имя кнопки, будет возвращено в сообщении `ShowDialogResponse`, `caption` — текст, отображаемый на кнопке. Максимальный размер поля `caption` — 100 символов. HTML-теги в нем не допускаются (будут экранированы).

После того, как пользователь нажимает кнопку в диалоге или принудительно закрывает хост-окно (нажимает на «крестик»), результат действий пользователя возвращается в сообщении `ShowDialogResponse`.

> Сообщение ShowDialogResponse (Пользователь нажимает кнопку **Нет**)

```
{
  "name": "ShowDialogResponse",
  "correlationId": 12345,
  "buttonName": "No",
  "dialogResolution": "normal"
}
```

Параметры ответа `ShowDialogResponse`:

- `correlationId` — идентификатор соответствующего сообщения `ShowDialogResponse`;
- `dialogResolution` — признак выбора: `normal` — была нажата одна из кнопок, `closedByUser` — диалог был завершен принудительно;
- `buttonName` — имя выбранной кнопки.

> Сообщение ShowDialogResponse (Пользователь закрыл диалог, нажав на «крестик»)

```
{
  "name": "ShowDialogResponse",
  "correlationId": 12345,
  "dialogResolution": "closedByUser"
}
```

**Примечание**: В версии Google Chrome 92.0 и выше использование браузерных диалоговых окон через вызовы Window.alert(), Window.confirm() из iframe [запрещено](https://www.chromestatus.com/feature/5148698084376576). Поэтому рекомендуется использовать сервис стандартных диалогов МоегоСклада.

#### Протокол навигации

> Дескриптор с виджетом, использующим протокол навигации

```
<ServerApplication  xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"             
                    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"             
                    xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2      
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
    <iframe>
        <sourceUrl>https://example.com/iframe.html</sourceUrl>
        <expand>true</expand>
    </iframe>
    <vendorApi>
        <endpointBase>https://example.com/dummy-app</endpointBase>
    </vendorApi>
    <access>
        <resource>https://api.moysklad.ru/api/remap/1.2</resource>
        <scope>admin</scope>
    </access>
    <widgets>        
        <entity.counterparty.edit>            
            <sourceUrl>https://example.com/widget.php</sourceUrl>            
            <height>                
                <fixed>150px</fixed>            
            </height>
            <uses>
                <navigation-service/>
            </uses>                  
        </entity.counterparty.edit>    
    </widgets>
</ServerApplication>
```

> Дескриптор решения, у которого главное и модальное окно используют протокол навигации

```
<ServerApplication xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2      
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
  <iframe>
    <sourceUrl>https://example.com/iframe.html</sourceUrl>
    <expand>true</expand>
    <uses>
      <navigation-service/>
    </uses>
  </iframe>
  <vendorApi>
    <endpointBase>https://example.com/dummy-app</endpointBase>
  </vendorApi>
  <access>
    <resource>https://api.moysklad.ru/api/remap/1.2</resource>
    <scope>admin</scope>
  </access>
  <popups>
    <popup>
      <name>coolPopup</name>
      <sourceUrl>https://vendorurl.coolpopup.ru</sourceUrl>
      <uses>
        <navigation-service/>
      </uses>
    </popup>
  </popups>
</ServerApplication>
```

Позволяет виджетам, главному и модальным окнам решений осуществлять переход на другую страницу МоегоСклада и открывать МойСклад в новой вкладке.

Чтобы виджет, iframe или модальное окно начали поддерживать протокол навигации в дескрипторе необходимо добавить в блок `uses` для `widgets`, `iframe` или `popup` тег: `<navigation-service/>`. Примеры смотрите в правой части экрана.

Рассмотрим пример с виджетом. Когда виджет отправляет хост-окну сообщение `NavigateRequest` (через Window.postMessage), хост-окно переходит на другую страницу МоегоСклада или открывает в новой вкладке браузера нужную страницу МоегоСклада.

> Cообщение NavigateRequest

```
{
  "name": "NavigateRequest",
  "messageId": 12345,
  "path": "#good/edit?id=e8a46787-0ff4-11ec-0a80-1eb200000740",
  "target": "blank"
}
```

Параметры сообщения `NavigateRequest`:

- `messageId` — целочисленный идентификатор сообщения, уникальный в рамках текущего взаимодействия виджет — хост-окно. Назначается виджетом.
- `path` — путь до страницы, на которую виджет хочет осуществить переход. Например, чтобы осуществить переход пользователя на страницу реестра заказов покупателя https://online.moysklad.ru/app/#customerorder, нужно передать `#customerorder`.
- `target` — вид навигации. Может принимать одно из двух значений: `self` — переход в текущей вкладке, `blank` — открытие в новой вкладке.

Если валидация сообщения пройдет успешно, перед переходом пользователя будет отправлен `NavigateResponse` обратно в виджет.

> Cообщение NavigateResponse

```
{
  "name": "NavigateResponse",
  "correlationId": 12345 
}
```

Параметры ответа `NavigateResponse`:

- `correlationId` — идентификатор соответствующего сообщения `NavigateRequest`.

При навигации из модального окна в текущей вкладке (`target` имеет значение `self`) произойдет переход, и модальное окно будет отображаться поверх страницы. Если необходимо, чтобы после перехода окно закрывалось, используйте сообщение `ClosePopup`. Подробнее смотрите в разделе [Кастомные модальные окна](https://dev.moysklad.ru/doc/api/vendor/1.0/#kastomnye-modal-nye-okna).

### SDK для виджетов

Для упрощения работы с протоколами виджетов и сервисами хост-окна можно использовать **JS Widget SDK**. Данный SDK дает удобный API для запросов (request/response) и событий хоста, скрывая работу с `messageId`/`correlationId` и обработку ошибок `InvalidMessageError`.

Ссылка на репозиторий SDK:

- GitHub: [https://github.com/moysklad/js-widget-sdk](https://github.com/moysklad/js-widget-sdk)

Подробности и актуальные примеры смотрите в README репозитория.

**Подключение**:

```
<script src="https://cdn.jsdelivr.net/npm/@moysklad-official/js-widget-sdk/dist/widget.min.js"></script>
```

Варианты фиксации версии:

- с фиксацией мажорной версии: `https://cdn.jsdelivr.net/npm/@moysklad-official/js-widget-sdk@1/dist/widget.min.js`
- с фиксацией конкретной версии: `https://cdn.jsdelivr.net/npm/@moysklad-official/js-widget-sdk@1.0.0/dist/widget.min.js`

**Быстрый старт**:

```
const sdk = WidgetSDK.create({ debug: true });

sdk.onOpen((message) => {
  console.log('Open', message);
});

sdk.showDialog('Учетная запись будет удалена. Вы хотите продолжить?', [
  { name: 'Yes', caption: 'Да, удалить' },
  { name: 'No', caption: 'Нет' }
]).then((response) => {
  console.log('Dialog response', response);
});
```

Параметр `debug` используйте только при разработке. В продакшене его следует отключать.

**Соответствие протоколам** (используйте только при наличии поддержки в дескрипторе):

- `openFeedback()` — **open-feedback**
- `setDirty()` / `clearDirty()` — **dirty-state**
- `onChange()` + `validationFeedback()` — **change-handler** + **validation-feedback**
- `update()` — **update-provider**
- `showPopup()` / `closePopup()` — **popups**
- `selectGoodFolder()` — **good-folder-selector**
- `showDialog()` — **standard-dialogs**
- `navigateTo()` — **navigation-service**

SDK рассчитан на работу в браузерном окружении (iframe) и не предназначен для использования на сервере. Поддерживаются последние версии браузеров: Яндекс.Браузер, Chrome, Opera, Firefox, Safari.

### Ошибки при работе с виджетами

При получении сообщения от виджета хост-окно производит валидацию сообщения. Если проверка не пройдена, хост-окно возвращает в ответ сообщение `InvalidMessageError` со списком ошибок.

> Пример сообщения InvalidMessageError

```
{
  "name": "InvalidMessageError",
  "correlationId": null,
  "invalidMessage": {
    "name": "SelectGoodFolderRequest"
  },
  "errors": [
    {
      "code": 1001,
      "error": "Отсутствует обязательный параметр messageId"
    }
  ]
}
```

Пример такого сообщения смотрите в правой части экрана.

Здесь:

- `correlationId` — идентификатор сообщения, которое вызвало ошибку;
- `invalidMessage` — исходное сообщение, которое вызвало ошибку;
- `errors` — список ошибок, каждое из которых содержит поля:
    - `code` — код ошибки
    - `error` — описание ошибки.

Перечень возможных ошибок представлен в таблице.

|Код ошибки|Сообщение|Описание|Пример|
|---|---|---|---|
|1000|Недопустимое состояние виджета %state% для сообщения %message.name%, допустимые состояния: %message.expectedStates%|Не допускается отправка данного сообщения из текущего состояния виджета|`Недопустимое состояние виджета Opened для сообщения OpenFeedback, допустимые состояния: Opening`|
|1001|Отсутствует обязательный параметр %parameter.name%|В сообщении отсутствует обязательный параметр|`Отсутствует обязательный параметр messageId`|
|1002|Некорректное значение параметра %parameter.name%: %пояснение%|Параметр сообщения имеет некорректное значение|`Некорректное значение параметра popupName: popup with name = 'somePopup1' not found`|
|1003|Параллельный запрос %message.name%|Не допускается отправка виджетом повторного запроса до момента получения ответа на предыдущий такой же запрос. Например: виджет уже отправил `SelectGoodFolderRequest` и пользователь еще не завершил выбор|`Параллельный запрос SelectGoodFolderRequest`|
|1004|Виджет не поддерживает протокол для обработки сообщения %message.name%|Не допускается обработка сообщения в виджете, который не поддерживает протокол данного сообщения|`Виджет не поддерживает протокол для обработки сообщения ShowDialogRequest`|

### Ограничения для контента, загружаемого в виджетах

> Пример HTML-атрибутов iframe виджета, видимых в DevTools браузера

```
<iframe src="https://example.com/widget.html"
        sandbox="allow-forms allow-scripts allow-same-origin
                 allow-popups allow-modals allow-downloads"
        allow="clipboard-read; clipboard-write">
</iframe>
```

МойСклад загружает весь контент решения (главный iframe, виджеты и кастомные модальные окна) внутри sandboxed iframe с определёнными HTML-атрибутами `sandbox` и `allow`. Эти атрибуты устанавливаются платформой МоегоСклада и не могут быть изменены разработчиком.

Одинаковый набор sandbox-флагов применяется ко всем трём типам iframe: главному iframe, виджетам и кастомным модальным окнам. Единственное отличие — главный iframe дополнительно получает разрешение `fullscreen` в атрибуте `allow`.

#### Атрибут sandbox

Атрибут `sandbox` управляет ограничениями встроенного контента. Следующие флаги **включены** для всех iframe решений:

|Флаг|Описание|
|---|---|
|`allow-forms`|Разрешает отправку HTML-форм внутри iframe|
|`allow-scripts`|Разрешает выполнение JavaScript|
|`allow-same-origin`|Iframe может обращаться к cookies и storage своего домена|
|`allow-popups`|Разрешает `window.open()` и ссылки с `target="_blank"`|
|`allow-modals`|Разрешает `alert()`, `confirm()`, `prompt()` и элемент `<dialog>`|
|`allow-downloads`|Разрешает инициировать скачивание файлов|

Следующие флаги **не включены** в sandbox-политику. Соответствующие возможности заблокированы для кода решений:

|Флаг|Почему не включён|
|---|---|
|`allow-top-navigation`|Прямая навигация top-level страницы запрещена — разработчик не может программно перенаправить пользователя со страницы МоегоСклада. Для навигации по страницам МоегоСклада используйте протокол [navigation-service](https://dev.moysklad.ru/doc/api/vendor/1.0/#protokol-nawigacii)|
|`allow-top-navigation-by-user-activation`|Запрещено даже по клику пользователя — iframe решения не должен перехватывать клики для перенаправления top-level страницы. Используйте [navigation-service](https://dev.moysklad.ru/doc/api/vendor/1.0/#protokol-nawigacii).|
|`allow-top-navigation-to-custom-protocols`|Запрещена навигация на кастомные протоколы (`tel:`, `mailto:` и др.). Решение не должно инициировать открытие сторонних приложений из контекста МоегоСклада без контроля платформы.|
|`allow-popups-to-escape-sandbox`|Попапы, открытые из iframe через `window.open()`, наследуют те же sandbox-ограничения. Без этого ограничения решение могло бы через попап обойти sandbox-политику и получить привилегии, не предусмотренные платформой.|
|`allow-orientation-lock`|МойСклад — десктопное веб-приложение, блокировка ориентации экрана не применима в контексте сервиса.|
|`allow-pointer-lock`|Захват курсора мыши (Pointer Lock API, используется в играх и 3D-приложениях) не требуется для бизнес-логики решений. Кроме того, может ухудшить UX, неожиданно заблокировав курсор пользователя.|
|`allow-presentation`|Presentation API (трансляция на внешние дисплеи) не применим в контексте работы с МоимСкладом.|
|`allow-storage-access-by-user-activation`|Экспериментальная возможность браузера для доступа к unpartitioned cookies. Не требуется: решение работает с cookies и storage своего домена через `allow-same-origin`, доступ к cookies МоегоСклада не предусмотрен.|

#### Атрибут allow

Атрибут `allow` управляет доступом к API браузера внутри iframe:

|Тип iframe|Значение атрибута `allow`|
|---|---|
|Главный iframe|`clipboard-read; clipboard-write; fullscreen`|
|Виджет|`clipboard-read; clipboard-write`|
|Кастомное модальное окно (popup)|`clipboard-read; clipboard-write`|

`clipboard-read` и `clipboard-write` разрешают всем типам iframe работать с буфером обмена (копирование/вставка). Главный iframe дополнительно получает `fullscreen`, позволяющий использовать Fullscreen API (например, `element.requestFullscreen()`). Виджеты и модальные окна не получают `fullscreen`, так как являются встроенными компонентами, занимающими часть страницы.

### Кастомные кнопки

> Дескриптор решения с 3 кнопками на странице Заказа покупателя

```
   <ServerApplication xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"
                xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2
         https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
       <iframe>...</iframe>
       <vendorApi>...</vendorApi>
       <access>...</access>
        <buttons>
            <button name="button1" title="Отправить контрагенту">
                <locations>
                    <document.customerorder.edit/>
                </locations>
            </button>
            <button name="button2" title="Сформировать цифровую подпись">
                <locations>
                    <document.customerorder.create/>
                    <document.customerorder.edit/>
                </locations>
            </button>
            <button name="button3" title="Импортировать документы" useSelected="false">
                <locations>
                    <document.customerorder.list/>
                </locations>
            </button>
        </buttons>
</ServerApplication>
```

Кастомные кнопки позволяют пользователю выполнять дополнительные действия путем выбора элемента из меню Решения, расположенного в карточке или в списке документов (сущностей) МоегоСклада. Каждая кнопка запускает одно специфическое действие, инициируемое пользователем и выполняемое на сервере разработчика.

Примеры действий при нажатии на кастомную кнопку:

- Создать ссылку на оплату
- Проверить контрагента
- Открыть чат с клиентом
- Отправить выделенные документы в ЭДО
- Импортировать документы

Пример меню Решения:

![useful image](https://dev.moysklad.ru/doc/api/vendor/1.0/images/buttons.png)

Порядок отображения кнопок в меню соответствует имени решения (сортировка по алфавиту) и порядку задания кнопки в дескрипторе. Кнопки начинают отображаться на странице только после перехода решения в статус **Activated**.

Кнопки можно встраивать как в карточку документа (сущности) так и в списки. В случае работы с конкретным документом в контексте обработки нажатия придет идентификатор открытого объекта. При работе со списками в контексте обработки придет массив выделенных объектов и их типы.

В списках пользователю дается возможность выбрать до 100 объектов перед нажатием кнопки. Если требуется выполнить действие для большего количества объектов, предусмотрен диалог подтверждения, в котором можно выбрать все объекты с учётом фильтров:

![useful image](https://dev.moysklad.ru/doc/api/vendor/1.0/images/confirm-click-button.png)

Если кнопка не зависит от выбранных элементов, диалог подтверждения можно пропустить. См. атрибут `useSelected` в [buttons](https://dev.moysklad.ru/doc/api/vendor/1.0/#blok-buttons).

Для добавления кастомной кнопки в меню Решения необходимо заполнить [блок buttons](https://dev.moysklad.ru/doc/api/vendor/1.0/#blok-buttons) в дескрипторе и реализовать [эндпоинт обработчика нажатия на кнопку в vendorApi](https://dev.moysklad.ru/doc/api/vendor/1.0/#obrabotka-nazhatiq-na-kastomnuu-knopku).

Обработку нажатия можно сделать как синхронной (если время обработки известно и оно не превышает нескольких секунд), так и асинхронной (когда точное время обработки заранее не известно). Если при нажатии на кастомную кнопку запускается асинхронная обработка, то при ее окончании пользователю будет выведено всплывающее системное уведомление, которое также сохраняется в Ленте уведомлений.

Примеры асинхронных уведомлений:

![useful image](https://dev.moysklad.ru/doc/api/vendor/1.0/images/async_buttons_1.png)

![useful image](https://dev.moysklad.ru/doc/api/vendor/1.0/images/async_buttons_2.png)

### Действия в сценариях

Действия в сценариях позволяют администратору аккаунта настроить обработку дополнительных действий путем выбора из списка установленных решений (находящихся в статусе **Activated**). Настройка производится на странице [сценариев в МоемСкладе](https://online.moysklad.ru/app/#scripttemplate).

Пример настройки действия в сценариях:

![useful image](https://dev.moysklad.ru/doc/api/vendor/1.0/images/scenario-setup.png)

Для добавления действия нужно заполнить [блок scenario](https://dev.moysklad.ru/doc/api/vendor/1.0/#blok-scenario) в дескрипторе и реализовать [эндпоинт обработчика действия в vendorApi](https://dev.moysklad.ru/doc/api/vendor/1.0/#obrabotka-dejstwiq-w-scenarii).

Если при выполнении обработчика действия возникнет ошибка, пользователи увидят сообщение об этом в истории выполнения на странице редактирования сценария. Пример:

![useful image](https://dev.moysklad.ru/doc/api/vendor/1.0/images/scenario-audit.png)

Подробнее про сценарии можно прочитать на [странице Центра поддержки](https://support.moysklad.ru/hc/ru/%D0%B7%D0%BD%D0%B0%D0%BA%D0%BE%D0%BC%D1%81%D1%82%D0%B2%D0%BE%20%D1%81%20%D1%81%D0%B5%D1%80%D0%B2%D0%B8%D1%81%D0%BE%D0%BC/%D0%B8%D0%BD%D1%81%D1%82%D1%80%D1%83%D0%BC%D0%B5%D0%BD%D1%82%D1%8B/%D1%81%D1%86%D0%B5%D0%BD%D0%B0%D1%80%D0%B8%D0%B8).

### Дескриптор решения

Дескриптор решения — XML-структура, которая описывает технические параметры встраивания/интеграции решения разработчика в МойСклад.

Содержимое дескриптора должно соответствовать версии XSD-схемы. Актуальной версией считается [v2](https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd).

#### История версий XSD-схемы дескриптора

|Версия|Поддерживается|Описание|Разрешенное содержимое дескриптора|Поддерживаемые типы решений|
|---|---|---|---|---|
|1.0.0|⬜|Серверные и простые iFrame-решения|vendorApi, access, iframe|iFrame, Серверные|
|1.1.0|⬜|Расширение iFrame (тег expand)|vendorApi, access, iframe(c expand)|iFrame, Серверные|
|[v2](https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd)|✅|[Виджеты](https://dev.moysklad.ru/doc/api/vendor/1.0/#vidzhety) в документах и сущностях. Кастомные модальные окна. Гибкие права решений. Дополнительные и сервисные протоколы.|vendorApi, access(с permissions), iframe, iframes, widgets, popups|Серверные|

#### Содержимое дескриптора решения

```
<ServerApplication xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2    
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
  <vendorApi>
    <endpointBase>https://example.com/dummy-app</endpointBase>
  </vendorApi>
  <access>
    <resource>https://api.moysklad.ru/api/remap/1.2</resource>
    <scope>custom</scope>
    <permissions>
      <viewDashboard/>
      <customerOrder>
        <view/>
        <create/>
        <update/>
      </customerOrder>
    </permissions>
  </access>
</ServerApplication>
```

В [актуальной версии](https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd) дескриптора решения допустимы следующие блоки:

|Блок|Назначение|Требует наличия других блоков|Обязательный|
|---|---|---|---|
|vendorApi|Описывает взаимодействие по Vendor API|Нет|Да|
|access|Описывает требуемый доступ решения к ресурсам пользовательского аккаунта|Требует vendorApi|Да|
|loyaltyApi|Указывает на то, что решение поддерживает Loyalty API|Нет|Нет|
|fiscalApi|Указывает на то, что решение поддерживает Fiscal API|Нет|Нет|
|qrPayApi|Указывает на то, что решение поддерживает QrPay API|Нет|Нет|
|iframes|Описывает окна решения|Нет|Нет|
|iframe|Описывает главный iframe решения (устарел)|Нет|Нет|
|widgets|Описывает виджеты|Нет|Нет|
|popups|Описывает кастомные модальные окна|Нет|Нет|
|buttons|Описывает кастомные кнопки|Нет|Нет|
|scenario|Описывает действия в сценариях|Нет|Нет|

Порядок расположения этих блоков относительно друг друга в дескрипторе может быть произвольным.

#### Блок vendorApi

> Пример дескриптора с заполненным vendorApi:

```
<ServerApplication ...>
  <vendorApi>
    <endpointBase>https://example.com/dummy-app</endpointBase>
  </vendorApi>
  <access>...</access>
</ServerApplication>
```

В теге **vendorApi/endpointBase** указывается базовый URL эндпоинта на стороне разработчика, к которому будет обращаться МойСклад. В URL допускается использование только протокола HTTPS.

Для получения полного адреса конкретного эндпоинта Vendor API на стороне разработчика к базовому URL добавляется суффикс `/api/moysklad/vendor/1.0` и путь эндпоинта. Шаблон формирования полного URL ресурса в общем случае такой:

`{endpointBase}/api/moysklad/vendor/1.0/{endpointPath}/…`

Для эндпоинта активации/деактивации решений на аккаунте шаблон следующий (endpointPath = apps):

`{endpointBase}/api/moysklad/vendor/1.0/apps/{appId}/{accountId}`

Например, если:

- endpointBase = example.com/dummy-app
- appId = 5f3c5489-6a17-48b7-9fe5-b2000eb807fe
- accountId = f088b0a7-9490-4a57-b804-393163e7680f
- endpointPath = apps

то полный URL ресурса на стороне разработчика, к которому будет выполнять запросы МойСклад при активации и деактивации решения на аккаунте, будет следующим:

`https://example.com/dummy-app/api/moysklad/vendor/1.0/apps/5f3c5489-6a17-48b7-9fe5-b2000eb807fe/f088b0a7-9490-4a57-b804-393163e7680f`

В случае отсутствия блока vendorApi в дескрипторе не выполняется активация и деактивация решения на серверах разработчика.

##### Блок дополнительных событий

В блоке vendorApi опционально можно указать поддерживаемые разработчиком дополнительные события (additionalEvents).

Какие события реализованы:

- updatePermissions - Событие изменения прав установки, подробнее см. в разделе [События](https://dev.moysklad.ru/doc/api/vendor/1.0/#sobytie-izmeneniq-praw-ustanowki-updatepermissions)

> Пример блока vendorApi с событием обновления прав

```
<vendorApi>
    <endpointBase>https://example.com/dummy-app</endpointBase>
    <additionalEvents>
        <updatePermissions/>
    </additionalEvents>
</vendorApi>
```

#### Блок access

> Пример заполнения блока **access** с указанием прав Администратора:

```
<ServerApplication ...>
  <vendorApi>...</vendorApi>
  <access>
    <resource>https://api.moysklad.ru/api/remap/1.2</resource>
    <scope>admin</scope>
  </access>
</ServerApplication>

```

> Пример заполнения блока **access** с явным перечислением пермиссий:

```
<ServerApplication ...>
  <vendorApi>...</vendorApi>
  <access>
    <resource>https://api.moysklad.ru/api/remap/1.2</resource>
    <scope>custom</scope>
    <permissions>
      <viewDashboard/>
      <viewAudit/>
      <viewProductCostAndProfit/>
      <useOwnWebhooks/>
      <useAllAttributeMetadata/>      
      <customerOrder>
        <view/>
        <create/>
        <update/>
        <delete/>
        <approve/>
        <print/>
      </customerOrder>
      <company>
        <view/>
        <create/>
      </company>
    </permissions>
  </access>
</ServerApplication>
```

Требуется для серверных решений, которые хотят получить доступ по JSON API к ресурсам аккаунта. В случае отсутствия этого блока в дескрипторе решения при установке на аккаунт решению не выдаются никакие доступы к ресурсам. Наличие блока **access** требует наличия блока **vendorApi** для передачи токена к ресурсам аккаунта при активации решения по Vendor API.

В теге **access/resource** указывается ресурс, к которому решению нужен доступ. На текущий момент для ресурса возможно только одно значение: `https://api.moysklad.ru/api/remap/1.2`

В теге **access/scope** указывается требуемый уровень доступа. Для него на текущий момент доступно два значения: `admin` и `custom`.

- Если указан уровень `admin`, решение будет работать с правами администратора аккаунта.
- Если указан уровень `custom`, решение получит доступ только к отчетам, документам и сущностям, перечисленным в теге **permissions**.

В теге **access/permissions** указываются требуемые пермиссии. Данный тег обязателен для уровня доступа со значением `custom`.

Перечисленные в теге **permissions** права доступа делятся на три группы:

- **Пользовательские** — доступ к отчётам в МоемСкладе.
- **Специальные** — права для работы с вебхуками и дополнительными полями (подробнее: [вебхуки](https://dev.moysklad.ru/doc/api/vendor/1.0/#rabota-s-webhukami), [доп. поля](https://dev.moysklad.ru/doc/api/vendor/1.0/#rabota-s-dopolnitel-nymi-polqmi)).
- **Сущностей** — права к сущностям и документам с уровнями доступа (`view`, `create`, `update`, `delete`, `print`, `approve`).

Полный перечень поддерживаемых пермиссий и допустимых действий указан в таблицах ниже.

**Поддерживаемые пермиссии в дескрипторе решения**

Пользовательские пермиссии

|Название|Тег в дескрипторе|Описание|
|---|---|---|
|Просмотр дашборда|`<viewDashboard/>`|Доступ к показателям dashboard|
|Просмотр аудита|`<viewAudit/>`|Доступ к истории действий|
|Просмотр прибыльности|`<viewSaleProfit/>`|Прибыль по товарам, сотрудникам, контрагентам и пр.|
|Просмотр прибыли и убытков|`<viewProfitAndLoss/>`|Используется в отчёте о прибыли|
|Просмотр себестоимости и прибыли|`<viewProductCostAndProfit/>`|Цена закупки и себестоимость в документах|
|Просмотр CRM-показателей|`<viewCompanyCRM/>`|Показатели CRM для контрагентов|
|Просмотр оборотов|`<viewTurnover/>`|Доступ к отчёту об оборотах|
|Видеть остатки денег|`<viewMoneyDashboard/>`|Остатки на счетах и движение средств|
|Просматривать остатки по товарам|`<viewStockReport/>`|Просматривать отчеты по остаткам|
|Просматривать взаиморасчеты|`<viewCustomerBalanceList/>`|Просматривать отчеты по взаиморасчетам|

Специальные пермиссии

|Название|Тег в дескрипторе|Описание|
|---|---|---|
|Управление своими вебхуками|`<useOwnWebhooks/>`|Видеть/создавать/обновлять/удалять только свои вебхуки|
|Управление всеми вебхуками|`<useAllWebhooks/>`|Полный доступ ко всем вебхукам|
|Управление своими доп. полями|`<useOwnAttributeMetadata/>`|Видеть/создавать/обновлять/удалять только свои доп. поля|
|Управление всеми доп. полями|`<useAllAttributeMetadata/>`|Полный доступ ко всем дополнительным полям|

Пермиссии сущностей по уровням доступа

|Название сущности|Техническая сущность|Тип|Действия|Тег в дескрипторе|
|---|---|---|---|---|
|Бонусные баллы|bonusTransaction|OPERATION|`<view/>`|`<bonusTransaction>...</bonusTransaction>`|
|Валюты|currency|BASE|`<view/>`|`<currency>...</currency>`|
|Внесения|retailDrawerCashIn|OPERATION|`<update/>`, `<create/>`, `<view/>`|`<retailDrawerCashIn>...</retailDrawerCashIn>`|
|Внутренние заказы|internalOrder|OPERATION|`<view/>`|`<internalOrder>...</internalOrder>`|
|Возврат покупателя|salesReturn|OPERATION|`<update/>`, `<create/>`, `<print/>`, `<view/>`|`<salesReturn>...</salesReturn>`|
|Возвраты|retailSalesReturn|OPERATION|`<update/>`, `<approve/>`, `<print/>`, `<view/>`|`<retailSalesReturn>...</retailSalesReturn>`|
|Возвраты поставщикам|purchaseReturn|OPERATION|`<view/>`|`<purchaseReturn>...</purchaseReturn>`|
|Возвраты предоплат|prepaymentReturn|OPERATION|`<create/>`, `<approve/>`, `<print/>`, `<view/>`|`<prepaymentReturn>...</prepaymentReturn>`|
|Входящий платеж|paymentIn|OPERATION|`<approve/>`, `<print/>`, `<view/>`|`<paymentIn>...</paymentIn>`|
|Выданный отчет комиссионера|commissionReportOut|OPERATION|`<update/>`, `<create/>`, `<approve/>`, `<view/>`|`<commissionReportOut>...</commissionReportOut>`|
|Выплаты|retailDrawerCashOut|OPERATION|`<view/>`|`<retailDrawerCashOut>...</retailDrawerCashOut>`|
|Выполнение этапов|productionStageCompletion|DICTIONARY|`<delete/>`, `<update/>`, `<create/>`, `<print/>`, `<view/>`|`<productionStageCompletion>...</productionStageCompletion>`|
|Договоры|contract|DICTIONARY|`<view/>`|`<contract>...</contract>`|
|Единицы измерения|uom|BASE|`<view/>`|`<uom>...</uom>`|
|Заказы на производство|processingOrder|OPERATION|`<view/>`|`<processingOrder>...</processingOrder>`|
|Заказы покупателей|customerOrder|OPERATION|`<delete/>`, `<update/>`, `<approve/>`, `<view/>`|`<customerOrder>...</customerOrder>`|
|Заказы поставщикам|purchaseOrder|OPERATION|`<delete/>`, `<update/>`, `<create/>`, `<approve/>`, `<print/>`, `<view/>`|`<purchaseOrder>...</purchaseOrder>`|
|Инвентаризации|inventory|DICTIONARY|`<create/>`, `<print/>`, `<view/>`|`<inventory>...</inventory>`|
|Исходящий платеж|paymentOut|OPERATION|`<view/>`|`<paymentOut>...</paymentOut>`|
|Каналы продаж|salesChannel|BASE|`<delete/>`, `<update/>`, `<create/>`, `<view/>`|`<salesChannel>...</salesChannel>`|
|Контрагенты|company|DICTIONARY|`<print/>`, `<view/>`|`<company>...</company>`|
|Корректировки взаиморасчетов|counterpartyAdjustment|DICTIONARY|`<delete/>`, `<update/>`, `<create/>`, `<print/>`, `<view/>`|`<counterpartyAdjustment>...</counterpartyAdjustment>`|
|Оприходования|enter|OPERATION|`<update/>`, `<approve/>`, `<view/>`|`<enter>...</enter>`|
|Отгрузки|demand|OPERATION|`<delete/>`, `<update/>`, `<view/>`|`<demand>...</demand>`|
|Перемещения|move|OPERATION|`<create/>`, `<approve/>`, `<view/>`|`<move>...</move>`|
|Полученный отчет комиссионера|commissionReportIn|OPERATION|`<update/>`, `<create/>`, `<approve/>`, `<print/>`, `<view/>`|`<commissionReportIn>...</commissionReportIn>`|
|Пользовательские справочники|customEntity|BASE|`<delete/>`, `<update/>`, `<create/>`, `<view/>`|`<customDictionary>...</customDictionary>`|
|Прайс-листы|priceList|OPERATION|`<create/>`, `<view/>`|`<priceList>...</priceList>`|
|Предоплаты|prepayment|OPERATION|`<view/>`|`<prepayment>...</prepayment>`|
|Приемки|supply|OPERATION|`<delete/>`, `<update/>`, `<create/>`, `<print/>`, `<view/>`|`<supply>...</supply>`|
|Приходный ордер|cashIn|OPERATION|`<approve/>`, `<view/>`|`<cashIn>...</cashIn>`|
|Продажи|retailDemand|OPERATION|`<view/>`|`<retailDemand>...</retailDemand>`|
|Проекты|project|BASE|`<view/>`|`<project>...</project>`|
|Производственные задания|productionTask|OPERATION|`<delete/>`, `<update/>`, `<create/>`, `<approve/>`, `<print/>`, `<view/>`|`<productionTask>...</productionTask>`|
|Расходный ордер|cashOut|OPERATION|`<view/>`|`<cashOut>...</cashOut>`|
|Склады|warehouse|BASE|`<view/>`|`<warehouse>...</warehouse>`|
|Смены|retailShift|DICTIONARY|`<view/>`|`<retailShift>...</retailShift>`|
|Сотрудники|employee|BASE|`<view/>`|`<employee>...</employee>`|
|Списания|loss|OPERATION|`<update/>`, `<print/>`, `<view/>`|`<loss>...</loss>`|
|Ставки НДС|taxRate|BASE|`<delete/>`, `<update/>`, `<create/>`, `<view/>`|`<taxRate>...</taxRate>`|
|Страны|country|BASE|`<view/>`|`<country>...</country>`|
|Счета покупателям|invoiceOut|OPERATION|`<delete/>`, `<update/>`, `<print/>`, `<view/>`|`<invoiceOut>...</invoiceOut>`|
|Счета поставщиков|invoiceIn|OPERATION|`<delete/>`, `<update/>`, `<create/>`, `<approve/>`, `<view/>`|`<invoiceIn>...</invoiceIn>`|
|Счета-фактуры выданные|factureOut|OPERATION|`<view/>`|`<factureOut>...</factureOut>`|
|Счета-фактуры полученные|factureIn|OPERATION|`<delete/>`, `<update/>`, `<approve/>`, `<print/>`, `<view/>`|`<factureIn>...</factureIn>`|
|Техкарты|processingPlan|BASE|`<view/>`|`<processingPlan>...</processingPlan>`|
|Техоперации|processing|OPERATION|`<view/>`|`<processing>...</processing>`|
|Техпроцессы|processingProcess|BASE|`<delete/>`, `<update/>`, `<create/>`, `<view/>`|`<processingProcess>...</processingProcess>`|
|Товары и услуги|good|DICTIONARY|`<update/>`, `<view/>`|`<good>...</good>`|
|Точки продаж|retailStore|BASE|`<delete/>`, `<update/>`, `<create/>`, `<view/>`|`<retailStore>...</retailStore>`|
|Этапы|processingStage|BASE|`<delete/>`, `<update/>`, `<create/>`, `<view/>`|`<processingStage>...</processingStage>`|
|Юр. лица|myCompany|BASE|`<view/>`|`<myCompany>...</myCompany>`|

Подробнее о пермиссиях в МоемСкладе смотрите в [документации JSON API](https://dev.moysklad.ru/doc/api/remap/1.2/dictionaries/#suschnosti-sotrudnik-rabota-s-prawami-sotrudnika).

Примечания:

- Имеются два ограничения на сочетания пермиссий сущностей:
    - уровень доступа `<view/>` необходим, если есть другие уровни;
    - уровень доступа `<update/>` необходим, если требуется уровень `<delete/>`.
- При установке решения ему будет автоматически предоставлено право на просмотр справочника Валют (`<currency><view/></currency>`).
- В настоящий момент не поддерживается пермиссия для работы с Задачами (`script`). Решение, которое хочет получить доступ к ним, должно работать с правами администратора.
- В настоящий момент не поддерживаются пермиссии для работы с сущностями Маркировки: `crptCancellation`, `crptPackageCreation`, `crptPackageItemRemoval`, `crptPackageDisaggregation`, `GTINList`, `trackingCodeList`.

#### Блок loyaltyApi

> Пример дескриптора с поддержкой Loyalty API:

```
<ServerApplication ...>
  <vendorApi>...</vendorApi>
  <access>...</access>
  <loyaltyApi/>
</ServerApplication>
```

Тег **loyaltyApi** является опциональным и указывается пустым. Он информирует МойСклад о том, что решение поддерживает [Loyalty API](https://dev.moysklad.ru/doc/api/loyalty/1.0/#scenarij-raboty). Настройки лояльности для решения, установленного на аккаунте, могут быть переданы посредством эндпоинта **/loyalty** Vendor API. Подробнее в разделе [REST эндпоинты на стороне МоегоСклада](https://dev.moysklad.ru/doc/api/vendor/1.0/#rest-andpointy-na-storone-moegosklada).

#### Блок fiscalApi

> Пример дескриптора с поддержкой Fiscal API:

```
<ServerApplication ...>
  <vendorApi>...</vendorApi>
  <access>...</access>
  <fiscalApi>
    <endpointBase>https://vendor.com/api/fiscal</endpointBase>
    <operationTypes>
      <openShift/>
      <closeShift/>
      <retailDemand/>
      <prepayment/>
      <retailSalesReturn/>
      <retailDrawerCashIn/>
      <retailDrawerCashOut/>
      <prepaymentReturn/>
      <advance/>
      <advanceReturn/>
    </operationTypes>
    <paymentTypes>
      <cash/>
      <card/>
      <qr/>
      <cashCard/>
      <advance/>
      <prepaymentCash/>
      <prepaymentCard/>
      <prepaymentQr/>
    </paymentTypes>
  </fiscalApi>
</ServerApplication>
```

Тег **fiscalApi** является опциональным. Он информирует МойСклад о том, что решение является провайдером операции фискализации для розничных продаж.

В теге **fiscalApi/operationTypes** указываются поддерживаемые типы операций. В теге **fiscalApi/paymentTypes** указываются поддерживаемые типы оплат.

Полный список поддерживаемых операций см. в [документации FiscalApi](https://dev.moysklad.ru/doc/api/fiscal/1.0/#podderzhiwaemye-operacii).

#### Блок qrPayApi

> Пример дескриптора с поддержкой QrPayAPI:

```
<ServerApplication ...>
  <vendorApi>...</vendorApi>
  <access>...</access>
  <qrPayApi>
    <paymentProvider>
        <name>Some vendor's first payment provider</name>
        <qrType>MERCHANT_GENERATED_DYNAMIC</qrType>
        <endpointBase>https://some-vendor.ru/api/1/root</endpointBase>
    </paymentProvider>
    <paymentProvider>
        <name>Some vendor's second payment provider</name>
        <qrType>MERCHANT_GENERATED_DYNAMIC</qrType>
        <endpointBase>https://some-vendor.ru/api/2/root</endpointBase>
    </paymentProvider>
  </qrPayApi>
</ServerApplication>
```

Тег **qrPayApi** является опциональным. Он информирует МойСклад о том, что решение является провайдером оплаты по QR-коду.

В теге **qrPayApi/paymentProvider** указываются способы оплаты, поддерживаемые приложением. В теге **qrPayApi/paymentProvider/qrType** указываются поддерживаемые типы QR.

Полный список возможных значений qrType см. в [документации QRPay API](https://dev.moysklad.ru/doc/api/qr-pay/1.0/#kak-zapolnit-deskriptor).

#### Блок iframes

> Пример дескриптора с заполненным блоком iframes:

```
<ServerApplication ...>
  <vendorApi>...</vendorApi>
  <access>...</access>
  <iframes>
    <iframe type="main" sourceUrl="https://example.com/dummy-app/main.html">
      <uses>
        <good-folder-selector/>
      </uses>
    </iframe>
    <iframe type="chat" sourceUrl="https://example.com/dummy-app/chat.html"/>
  </iframes>

</ServerApplication>
```

Служит для задания списка окон решения, которые будут появляться на страницах МоегоСклада.

- Чтобы задать тип окна, используйте атрибут `iframe.type` (обязательный). В настоящий момент может принимать одно из двух значений: `main, chat`. Для каждого типа может быть указано не более одного окна.
- Чтобы задать URL, по которому будет загружаться содержимое iframe, используйте атрибут `iframe.sourceUrl` (обязательный). В URL допускается использование только протокола HTTPS.
- Чтобы задать дополнительные протоколы, которые будут использоваться в iframe, используйте тег `uses` (опциональный). В настоящий момент поддерживается только для типа `main`. Список поддерживаемых значений совпадает со списком [блока **uses** для виджетов](https://dev.moysklad.ru/doc/api/vendor/1.0/#blok-serwisnyh-protokolow-uses).

Подробнее про [окна решений](https://dev.moysklad.ru/doc/api/vendor/1.0/#okna-iframes).

#### Блок iframe

> Пример дескриптора с заполненным блоком iframe:

```
<ServerApplication ...>
  <vendorApi>...</vendorApi>
  <access>...</access>
  <iframe>
    <sourceUrl>https://example.com/dummy-app/iframe.html</sourceUrl>
    <expand>true</expand>
  </iframe>
</ServerApplication>
```

В теге **iframe/sourceUrl** указывается URL, по которому будет загружаться содержимое главного iframe внутри UI МоегоСклада. В URL допускается использование только протокола HTTPS.

В теге **iframe/expand** указывается `boolean` значение, которое должно быть установлено в true, если содержимое не умещается в минимальную допустимую высоту окна (768px).

Тег **uses** — опциональный. Предназначен для сервисных протоколов, используемых iframe. Список поддерживаемых значений совпадает со списком [блока **uses** для виджетов](https://dev.moysklad.ru/doc/api/vendor/1.0/#blok-serwisnyh-protokolow-uses).

Данный блок считается устаревшим. Вместо него следует использовать [блок **iframes**](https://dev.moysklad.ru/doc/api/vendor/1.0/#blok-iframes).

#### Блок widgets

> Блок widgets с точками расширения в карточке Контрагента и документе Заказ покупателя

```
<ServerApplication ...>
  <vendorApi>...</vendorApi>
  <access>...</access>
  <widgets>
    <entity.counterparty.edit>
      <sourceUrl>https://example.com/dummy-app/widget-counterparty.php</sourceUrl>
      <height>
        <fixed>200px</fixed>
      </height>
      <supports>
        <open-feedback/>
      </supports>
    </entity.counterparty.edit>

    <document.customerorder.create>
      <sourceUrl>https://example.com/dummy-app/widget-customerorder-validation.php</sourceUrl>
      <height>
        <!-- Скрытый виджет-->
        <fixed>0px</fixed>
      </height>
      <supports>
        <change-handler/>
        <validation-feedback/>
      </supports>
    </document.customerorder.create>

    <document.customerorder.edit>
      <sourceUrl>https://example.com/dummy-app/widget-customerorder.php</sourceUrl>
      <height>
        <fixed>50px</fixed>
      </height>
      <uses>
        <good-folder-selector/>
        <standard-dialogs/>
        <navigation-service/>
      </uses>
    </document.customerorder.edit>
  </widgets>
</ServerApplication>
```

Сейчас доступны следующие точки расширения:

- **entity.counterparty.edit** — карточка Контрагента
- **entity.product.edit** — карточка Товара
- **entity.variant.edit** — карточка Модификации
- **entity.service.edit** — карточка Услуги
- **entity.bundle.edit** — карточка Комплекта
- **entity.productfolder.edit** — карточка Группы товаров
- **document.customerorder.create** — новый документ Заказ покупателя (до первого сохранения)
- **document.customerorder.edit** — документ Заказ покупателя
- **document.demand.create** — новый документ Отгрузка (до первого сохранения)
- **document.demand.edit** — документ Отгрузка
- **document.invoiceout.create** — новый документ Счет покупателю (до первого сохранения)
- **document.invoiceout.edit** — документ Счет покупателю
- **document.invoicein.create** — новый документ Счет поcтавщика (до первого сохранения)
- **document.invoicein.edit** — документ Счет поcтавщика
- **document.processingorder.edit** — документ Заказ на производство
- **document.purchaseorder.edit** — документ Заказ поставщику
- **document.supply.create** — новый документ Приемка (до первого сохранения)
- **document.supply.edit** — документ Приемка
- **document.paymentin.edit** — документ Входящий платеж
- **document.paymentout.edit** — документ Исходящий платеж
- **document.cashin.edit** — документ Приходный ордер
- **document.cashout.edit** — документ Расходный ордер
- **document.move.create** — новый документ Перемещение (до первого сохранения)
- **document.move.edit** — документ Перемещение
- **document.loss.create** — новый документ Списание (до первого сохранения)
- **document.loss.edit** — документ Списание
- **document.enter.create** — новый документ Оприходование (до первого сохранения)
- **document.enter.edit** — документ Оприходование
- **document.internalorder.edit** — документ Внутренний заказ
- **document.inventory.edit** — документ Инвентаризация
- **document.purchasereturn.edit** — документ Возврат поставщику
- **document.salesreturn.create** — новый документ Возврат покупателя
- **document.salesreturn.edit** — документ Возврат покупателя
- **document.retaildemand.create** — новый документ Розничная продажа
- **document.retaildemand.edit** — документ Розничная продажа
- **document.retailsalesreturn.edit** — документ Розничный возврат
- **document.retaildrawercashin.edit** — документ Внесение денег
- **document.retaildrawercashout.edit** — документ Выплата денег
- **document.emissionorder.edit** — документ Заказ кодов маркировки

Сначала необходимо определить в блоке **widgets** точку расширения — указать страницу, где будет расположен виджет.

В одном дескрипторе может быть указано несколько точек расширения, то есть одно решение сможет создать сразу несколько виджетов на разных страницах. В то же время для решения действует правило: одна страница — один виджет. То есть, в дескрипторе может быть указано только по одной точке расширения каждого типа.

Тем не менее в итоге на одной странице может оказаться несколько виджетов (от разных решений).

Список тегов для точек расширения:

Тег **sourceUrl** — обязательный. Содержит URL, по которому загружается код виджета в iframe. В URL допускается использование только протокола HTTPS.

Тег **height** — обязательный. В теге **height/fixed** задается фиксированная высота виджета в пикселях, в формате `150px`.

Виджет можно скрыть, установив `height/fixed = 0px`. Скрытые виджеты не отображаются на страницах МоегоСклада, но могут использовать все допустимые [протоколы](https://dev.moysklad.ru/doc/api/vendor/1.0/#protokoly-widzhetow).

#### Блок дополнительных протоколов (supports)

Блок **supports** — опциональный. Предназначен для дополнительных протоколов, поддерживаемых виджетом. На данный момент в нем можно указать протоколы:

- **open-feedback** — при открытии экрана обеспечивает скрытие содержимого виджета до явного уведомления от виджета о готовности. Параметры у протокола отсутствуют.
- **save-handler** — при сохранении сущности или объекта позволяет уведомить об этом виджет. Параметры у протокола отсутствуют.
- **dirty-state** — при наличии несохраненных изменений в виджете позволяет отобразить диалог подтверждения сохранения изменений. Параметры у протокола отсутствуют.
- **change-handler** — при изменении несохраненного состояния объекта позволяет уведомить об этом виджет, отправляя текущее состояние объекта. Параметры:
    - **validation-feedback** — виджет поддерживает протокол валидации. Хост-окно будет ожидать от виджета сообщение `ValidationFeedback` в ответ на сообщение `Change`.
- **update-provider** — позволяет менять текущее состояние объекта отправляя сообщение `UpdateRequest` из виджета. Параметры у протокола отсутствуют.

#### Доступность дополнительных протоколов в зависимости от точек встраивания

|Точка встраивания|open-feedback|save-handler|dirty-state|change-handler|validation-feedback|update-provider|
|---|---|---|---|---|---|---|
|_entity.counterparty.edit_|✅|✅|✅|⬜|⬜|⬜|
|_entity.product.edit_|✅|✅|✅|⬜|⬜|⬜|
|_entity.variant.edit_|✅|✅|✅|⬜|⬜|⬜|
|_entity.service.edit_|✅|✅|✅|⬜|⬜|⬜|
|_entity.bundle.edit_|✅|✅|✅|⬜|⬜|⬜|
|_entity.productfolder.edit_|✅|✅|✅|⬜|⬜|⬜|
|_document.customerorder.create_|⬜|⬜|⬜|✅|✅|⬜|
|_document.customerorder.edit_|✅|✅|✅|✅|✅|✅|
|_document.demand.create_|⬜|⬜|⬜|✅|✅|⬜|
|_document.demand.edit_|✅|✅|✅|✅|✅|✅|
|_document.invoiceout.create_|⬜|⬜|⬜|✅|✅|⬜|
|_document.invoiceout.edit_|✅|✅|✅|✅|✅|✅|
|_document.invoicein.create_|⬜|⬜|⬜|✅|✅|⬜|
|_document.invoicein.edit_|✅|✅|✅|✅|✅|⬜|
|_document.processingorder.edit_|✅|✅|✅|⬜|⬜|⬜|
|_document.purchaseorder.edit_|✅|✅|✅|⬜|⬜|⬜|
|_document.supply.create_|⬜|⬜|⬜|✅|✅|⬜|
|_document.supply.edit_|✅|✅|✅|✅|✅|✅|
|_document.paymentin.edit_|✅|✅|✅|⬜|⬜|⬜|
|_document.paymentout.edit_|✅|✅|✅|⬜|⬜|⬜|
|_document.cashin.edit_|✅|✅|✅|⬜|⬜|⬜|
|_document.cashout.edit_|✅|✅|✅|⬜|⬜|⬜|
|_document.move.create_|⬜|⬜|⬜|✅|✅|⬜|
|_document.move.edit_|✅|✅|✅|✅|✅|✅|
|_document.loss.create_|⬜|⬜|⬜|✅|✅|⬜|
|_document.loss.edit_|✅|✅|✅|✅|✅|✅|
|_document.enter.create_|⬜|⬜|⬜|✅|✅|⬜|
|_document.enter.edit_|✅|✅|✅|✅|✅|✅|
|_document.internalorder.edit_|✅|✅|✅|⬜|⬜|⬜|
|_document.inventory.edit_|✅|✅|✅|⬜|⬜|⬜|
|_document.purchasereturn.edit_|✅|✅|✅|⬜|⬜|⬜|
|_document.salesreturn.create_|⬜|⬜|⬜|✅|✅|⬜|
|_document.salesreturn.edit_|✅|✅|✅|✅|✅|⬜|
|_document.retaildemand.create_|⬜|⬜|⬜|✅|✅|⬜|
|_document.retaildemand.edit_|✅|✅|✅|✅|✅|⬜|
|_document.retailsalesreturn.edit_|✅|✅|✅|⬜|⬜|⬜|
|_document.retaildrawercashin.edit_|✅|✅|✅|⬜|⬜|⬜|
|_document.retaildrawercashout.edit_|✅|✅|✅|⬜|⬜|⬜|
|_document.emissionorder.edit_|✅|✅|✅|✅|✅|✅|

Подробнее о дополнительных протоколах читайте в разделе [Как работают виджеты](https://dev.moysklad.ru/doc/api/vendor/1.0/#kak-rabotaut-widzhety).

#### Блок сервисных протоколов (uses)

Блок **uses** — опциональный. Предназначен для сервисных протоколов, используемых виджетом. На данный момент в нем можно указать следующие протоколы:

- **good-folder-selector** позволяет виджетам решений переиспользовать существующий в МоемСкладе селектор группы товаров. При этом виджет получает результат выбора пользователя. Параметры у протокола отсутствуют. Подробнее про протокол можно прочитать в разделе [Селектор группы товаров](https://dev.moysklad.ru/doc/api/vendor/1.0/#selektor-gruppy-towarow).
- **standard-dialogs** позволяет виджетам решений использовать стандартные диалоги, существующие в МоемСкладе. При этом виджет получает результат выбора пользователя (кнопка, нажатая пользователем). Параметры у протокола отсутствуют. Подробнее о протоколе читайте в разделе [Стандартные диалоги](https://dev.moysklad.ru/doc/api/vendor/1.0/#standartnye-dialogi).
- **navigation-service** позволяет виджетам решений осуществлять переход на другую страницу МоегоСклада и открывать МойСклад в новой вкладке. Параметры у протокола отсутствуют. Подробнее о протоколе читайте в разделе [Протокол навигации](https://dev.moysklad.ru/doc/api/vendor/1.0/#protokol-nawigacii).

Пример заполненного блока **widgets** можно увидеть справа.

#### Блок popups

> Блок popups с двумя модальными окнами, одно из которых использует протокол good-folder-selector

```
<ServerApplication ...>
  <vendorApi>...</vendorApi>
  <access>...</access>
  <popups>
    <popup>
      <name>somePopup1</name>
      <sourceUrl>https://example.com/dummy-app/popup-1.php</sourceUrl>
    </popup>
    <popup>
      <name>somePopup2</name>
      <sourceUrl>https://example.com/dummy-app/popup-2.php</sourceUrl>
      <uses>
        <good-folder-selector/>
      </uses>
    </popup>
  </popups>
</ServerApplication>
```

Служит для задания списка кастомных модальных окон, которые могут использоваться решением в виджетах (блок widgets) и главном окне (блок iframes).

- Чтобы задать имя модального окна, используйте тег `name` (обязательный).
- Чтобы задать адрес страницы, используйте тег `sourceUrl` (обязательный).

Тег **uses** — опциональный. Предназначен для сервисных протоколов, используемых модальным окном. Список поддерживаемых значений совпадает со списком [блока **uses** для виджетов](https://dev.moysklad.ru/doc/api/vendor/1.0/#blok-serwisnyh-protokolow-uses).

Подробнее о работе с кастомными модальными окнами читайте в разделе [Кастомные модальные окна](https://dev.moysklad.ru/doc/api/vendor/1.0/#kastomnye-modal-nye-okna).

#### Блок buttons

> Блок buttons с кнопками в Заказе покупателя, Заказе поставщику и списке Контрагентов

```
<ServerApplication ...>
  <vendorApi>...</vendorApi>
  <access>...</access>
  <buttons>
    <button name="button1" title="Отправить контрагенту">
      <locations>
        <document.customerorder.edit/>
      </locations>
    </button>
    <button name="button2" title="Сформировать цифровую подпись">
      <locations>
        <document.customerorder.edit/>
        <document.purchaseorder.edit/>
      </locations>
    </button>
    <button name="button3" title="Проверить контрагентов">
      <locations>
        <entity.counterparty.list/>
      </locations>
    </button>
    <button name="button4" title="Импортировать заказы" useSelected="false">
      <locations>
        <document.customerorder.list/>
      </locations>
    </button>
  </buttons>
</ServerApplication>
```

Служит для задания списка кастомных кнопок, которые будут появляться на страницах МоегоСклада.

- Чтобы задать имя кнопки, отправляемое на сервер при нажатии, используйте атрибут `button.name` (обязательный).
- Чтобы задать заголовок кнопки, отображаемый в меню, используйте атрибут `button.title` (обязательный).
- Чтобы задать точки встраивания (страницы), на которых нужно показывать кнопку, используйте тег `locations` (обязательный).
- Чтобы на страницах списков отключить проверку наличия выбранных элементов и пропустить диалог подтверждения массовой операции, используйте атрибут `useSelected` (опциональный, по умолчанию `true`). При `false` выбранные элементы не учитываются.

Сейчас доступны следующие точки встраивания:

- **entity.counterparty.edit** — карточка Контрагента
- **entity.counterparty.list** — список Контрагентов
- **entity.product.edit** — карточка Товара
- **entity.variant.edit** — карточка Модификации
- **entity.service.edit** — карточка Услуги
- **entity.bundle.edit** — карточка Комплекта
- **entity.productfolder.edit** — карточка Группы товаров
- **entity.good.list** — список Товаров и услуг
- **document.customerorder.create** — новый документ Заказ покупателя (до первого сохранения)
- **document.customerorder.edit** — документ Заказ покупателя
- **document.customerorder.list** — список Заказов покупателей
- **document.demand.edit** — документ Отгрузка
- **document.demand.list** — список Отгрузок
- **document.invoiceout.edit** — документ Счет покупателю
- **document.invoiceout.list** — список Счетов покупателям
- **document.purchaseorder.edit** — документ Заказ поставщику
- **document.purchaseorder.list** — список Заказов поставщикам
- **document.retaildemand.edit** — документ Розничная продажа
- **document.retaildemand.list** — список Розничных продаж
- **document.finance.list** — список Платежей
- **document.invoicein.list** — список Счетов поставщиков
- **document.supply.list** — список Приемок
- **document.move.edit** — документ Перемещение
- **document.move.list** — список Перемещений
- **document.enter.edit** — документ Оприходование
- **document.enter.list** — список Оприходований
- **document.paymentin.edit** — документ Входящий платеж
- **document.paymentout.edit** — документ Исходящий платеж
- **document.salesreturn.list** — список Возвратов покупателей
- **document.salesreturn.edit** — документ Возврат покупателя
- **document.internalorder.list** — список Внутренних заказов
- **document.internalorder.edit** — документ Внутренний заказ
- **document.loss.list** — список Списаний
- **document.loss.edit** — документ Списание
- **document.emissionorder.edit** — документ Заказ кодов маркировки

Пример заполненного блока **buttons** можно увидеть справа.

Подробнее о работе с кастомными кнопками читайте в разделе [Кастомные кнопки](https://dev.moysklad.ru/doc/api/vendor/1.0/#kastomnye-knopki).

#### Блок scenario

> Блок scenario с двумя действиями

```
<ServerApplication ...>
  <vendorApi>...</vendorApi>
  <access>...</access>
  <scenario>
    <action name="create_cdek_invoice" title="Создать накладную СДЭК"/>
    <action name="send_telegram_message" title="Отправить сообщение в Telegram"/>
  </scenario>
</ServerApplication>
```

Служит для задания списка действий в сценариях, которые можно будет выбрать на странице настройки сценария в МоемСкладе.

- Чтобы задать имя действия, отправляемое на сервер при срабатывании сценария, используйте атрибут `action.name` (обязательный).
- Чтобы задать название действия, отображаемое в МоемСкладе, используйте атрибут `action.title` (обязательный).

Пример заполненного блока **scenario** можно увидеть справа.

Подробнее о работе со сценариями читайте в разделе [Действия в сценариях](https://dev.moysklad.ru/doc/api/vendor/1.0/#dejstwiq-w-scenariqh).

### Примеры дескрипторов

#### Для серверных решений (актуальная версия схемы дескриптора v2)

> Дескриптор для серверных решений

```

<ServerApplication xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2    
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
  <vendorApi>
    <endpointBase>https://example.com/dummy-app</endpointBase>
  </vendorApi>
  <access>
    <resource>https://api.moysklad.ru/api/remap/1.2</resource>
    <scope>admin</scope>
  </access>
</ServerApplication>
```

> Дескриптор для серверных решений с главным окном и окном чата

```

<ServerApplication xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2    
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
  <iframes>
    <iframe type="main" sourceUrl="https://example.com/dummy-app/main.html">
      <uses>
        <standard-dialogs/>
      </uses>
    </iframe>
    <iframe type="chat" sourceUrl="https://example.com/dummy-app/chat.html"/>
  </iframes>
  <vendorApi>
    <endpointBase>https://example.com/dummy-app</endpointBase>
  </vendorApi>
  <access>
    <resource>https://api.moysklad.ru/api/remap/1.2</resource>
    <scope>admin</scope>
  </access>
</ServerApplication>
```

> Дескриптор для серверных решений с виджетом в карточке Контрагента

```

<ServerApplication xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2    
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
  <iframe>
    <sourceUrl>https://example.com/dummy-app/iframe.html</sourceUrl>
  </iframe>
  <vendorApi>
    <endpointBase>https://example.com/dummy-app</endpointBase>
  </vendorApi>
  <access>
    <resource>https://api.moysklad.ru/api/remap/1.2</resource>
    <scope>admin</scope>
  </access>
  <widgets>
    <entity.counterparty.edit>
      <sourceUrl>https://example.com/dummy-app/widget.php</sourceUrl>
      <height>
        <fixed>150px</fixed>
      </height>
    </entity.counterparty.edit>
  </widgets>
</ServerApplication>
```

> Дескриптор для серверных решений с виджетом и протоколами open-feedback, save-handler, change-handler в Карточке контрагента, Заказе покупателя и Отгрузке

```

<ServerApplication xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2    
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
  <iframe>
    <sourceUrl>https://example.com/dummy-app/iframe.html</sourceUrl>
  </iframe>
  <vendorApi>
    <endpointBase>https://example.com/dummy-app</endpointBase>
  </vendorApi>
  <access>
    <resource>https://api.moysklad.ru/api/remap/1.2</resource>
    <scope>admin</scope>
  </access>
  <widgets>
    <entity.counterparty.edit>
      <sourceUrl>https://example.com/dummy-app/widget.php</sourceUrl>
      <height>
        <fixed>150px</fixed>
      </height>
      <supports>
        <open-feedback/>
      </supports>
    </entity.counterparty.edit>

    <document.customerorder.edit>
      <sourceUrl>https://example.com/dummy-app/widget-customerorder.php</sourceUrl>
      <height>
        <fixed>50px</fixed>
      </height>
      <supports>
        <open-feedback/>
        <save-handler/>
        <change-handler/>
      </supports>
    </document.customerorder.edit>

    <document.demand.edit>
      <sourceUrl>https://example.com/dummy-app/widget-demand.php</sourceUrl>
      <height>
        <fixed>50px</fixed>
      </height>
      <supports>
        <open-feedback/>
        <change-handler/>
      </supports>
    </document.demand.edit>
  </widgets>
</ServerApplication>
```

> Дескриптор для серверных решений с виджетом и протоколом change-handler c validation-feedback в Заказе покупателя

```

<ServerApplication xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2    
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
  <iframe>
    <sourceUrl>https://example.com/dummy-app/iframe.html</sourceUrl>
  </iframe>
  <vendorApi>
    <endpointBase>https://example.com/dummy-app</endpointBase>
  </vendorApi>
  <access>
    <resource>https://api.moysklad.ru/api/remap/1.2</resource>
    <scope>admin</scope>
  </access>
  <widgets>
    <document.customerorder.edit>
      <sourceUrl>https://example.com/dummy-app/widget-customerorder.php</sourceUrl>
      <height>
        <fixed>50px</fixed>
      </height>
      <supports>
        <change-handler>
          <validation-feedback/>
        </change-handler>
      </supports>
    </document.customerorder.edit>
  </widgets>
</ServerApplication>
```

> Дескриптор для серверных решений с виджетом и протоколами good-folder-selector и dirty-state в карточке Контрагента, Заказе покупателя и Отгрузке

```

<ServerApplication xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2    
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
  <iframe>
    <sourceUrl>https://example.com/dummy-app/iframe.html</sourceUrl>
  </iframe>
  <vendorApi>
    <endpointBase>https://example.com/dummy-app</endpointBase>
  </vendorApi>
  <access>
    <resource>https://api.moysklad.ru/api/remap/1.2</resource>
    <scope>admin</scope>
  </access>
  <widgets>
    <entity.counterparty.edit>
      <sourceUrl>https://example.com/dummy-app/widget.php</sourceUrl>
      <height>
        <fixed>150px</fixed>
      </height>
      <supports>
        <dirty-state/>
      </supports>
      <uses>
        <good-folder-selector/>
      </uses>
    </entity.counterparty.edit>

    <document.customerorder.edit>
      <sourceUrl>https://example.com/dummy-app/widget-customerorder.php</sourceUrl>
      <height>
        <fixed>50px</fixed>
      </height>
      <supports>
        <dirty-state/>
      </supports>
      <uses>
        <good-folder-selector/>
      </uses>
    </document.customerorder.edit>

    <document.demand.edit>
      <sourceUrl>https://example.com/dummy-app/widget-demand.php</sourceUrl>
      <height>
        <fixed>50px</fixed>
      </height>
      <supports>
        <dirty-state/>
      </supports>
      <uses>
        <good-folder-selector/>
      </uses>
    </document.demand.edit>
  </widgets>
</ServerApplication>
```

> Дескриптор для серверных решений с виджетом в Заказе покупателя и Счете покупателю

```

<ServerApplication xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2    
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
  <iframe>
    <sourceUrl>https://example.com/dummy-app/iframe.html</sourceUrl>
  </iframe>
  <vendorApi>
    <endpointBase>https://example.com/dummy-app</endpointBase>
  </vendorApi>
  <access>
    <resource>https://api.moysklad.ru/api/remap/1.2</resource>
    <scope>admin</scope>
  </access>
  <widgets>
    <document.customerorder.edit>
      <sourceUrl>https://example.com/dummy-app/widget-customerorder.php</sourceUrl>
      <height>
        <fixed>150px</fixed>
      </height>
    </document.customerorder.edit>
    <document.invoiceout.edit>
      <sourceUrl>https://example.com/dummy-app/widget-invoiceout.php</sourceUrl>
      <height>
        <fixed>110px</fixed>
      </height>
    </document.invoiceout.edit>
  </widgets>
</ServerApplication>
```

> Дескриптор для серверных решений с виджетом в Заказе покупателя и двумя кастомными модальными окнами, одно из которых поддерживает протокол good-folder-selector

```

<ServerApplication xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2    
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
  <iframe>
    <sourceUrl>https://example.com/dummy-app/iframe.html</sourceUrl>
  </iframe>
  <vendorApi>
    <endpointBase>https://example.com/dummy-app</endpointBase>
  </vendorApi>
  <access>
    <resource>https://api.moysklad.ru/api/remap/1.2</resource>
    <scope>admin</scope>
  </access>
  <widgets>
    <document.customerorder.edit>
      <sourceUrl>https://example.com/dummy-app/widget-customerorder.php</sourceUrl>
      <height>
        <fixed>150px</fixed>
      </height>
    </document.customerorder.edit>
  </widgets>
  <popups>
    <popup>
      <name>viewPopup</name>
      <sourceUrl>https://example.com/dummy-app/view-popup.php</sourceUrl>
    </popup>
    <popup>
      <name>editPopup</name>
      <sourceUrl>https://example.com/dummy-app/edit-popup.php</sourceUrl>
      <uses>
        <good-folder-selector/>
      </uses>
    </popup>
  </popups>
</ServerApplication>
```

> Дескриптор для серверных решений с кнопками в Заказе покупателя, Заказе поставщику, карточке Контрагента и Товара

```

   <ServerApplication xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"
                xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2
         https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
  <iframe>
    <sourceUrl>https://example.com/dummy-app/iframe.html</sourceUrl>
  </iframe>
  <vendorApi>
    <endpointBase>https://example.com/dummy-app</endpointBase>
  </vendorApi>
  <access>
    <resource>https://api.moysklad.ru/api/remap/1.2</resource>
    <scope>admin</scope>
  </access>
  <buttons>
    <button name="button1" title="Отправить контрагенту">
      <locations>
        <document.customerorder.edit/>
      </locations>
    </button>
    <button name="button2" title="Сформировать цифровую подпись">
      <locations>
        <entity.counterparty.edit/>
        <entity.product.edit/>
        <document.customerorder.edit/>
        <document.purchaseorder.edit/>
      </locations>
    </button>
  </buttons>
</ServerApplication>
```

> Дескриптор для серверных решений с явным указанием прав доступа

```

<ServerApplication xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2    
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
  <iframe>
    <sourceUrl>https://example.com/dummy-app/iframe.html</sourceUrl>
  </iframe>
  <vendorApi>
    <endpointBase>https://example.com/dummy-app</endpointBase>
  </vendorApi>
  <access>
    <resource>https://api.moysklad.ru/api/remap/1.2</resource>
    <scope>custom</scope>
    <permissions>
      <viewDashboard/>
      <viewAudit/>
      <purchaseOrder>
        <view/>
        <create/>
        <update/>
        <delete/>
        <print/>
        <approve/>
      </purchaseOrder>
      <good>
        <view/>
        <create/>
        <print/>
      </good>
    </permissions>
  </access>
</ServerApplication>
```

> Дескриптор для решений, работающих с webhooks

```

<ServerApplication xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2    
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
  <iframe>
    <sourceUrl>https://example.com/dummy-app/iframe.html</sourceUrl>
  </iframe>
  <vendorApi>
    <endpointBase>https://example.com/dummy-app</endpointBase>
  </vendorApi>
  <access>
    <resource>https://api.moysklad.ru/api/remap/1.2</resource>
    <scope>custom</scope>
    <permissions>
      <useOwnWebhooks/>
      <!-- Используйте <useAllWebhooks/> если решение должно управлять вебхуками всех решений -->
    </permissions>
  </access>
</ServerApplication>
```

> Дескриптор для решений, работающих с дополнительными полями

```

<ServerApplication xmlns="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                   xsi:schemaLocation="https://apps-api.moysklad.ru/xml/ns/appstore/app/v2    
                    https://apps-api.moysklad.ru/xml/ns/appstore/app/v2/application-v2.xsd">
  <iframe>
    <sourceUrl>https://example.com/dummy-app/iframe.html</sourceUrl>
  </iframe>
  <vendorApi>
    <endpointBase>https://example.com/dummy-app</endpointBase>
  </vendorApi>
  <access>
    <resource>https://api.moysklad.ru/api/remap/1.2</resource>
    <scope>custom</scope>
    <permissions>
      <useOwnAttributeMetadata/>
      <!-- Используйте <useAllAttributeMetadata/> если решение должно управлять доп.полями всех решений -->
    </permissions>
  </access>
</ServerApplication>
```