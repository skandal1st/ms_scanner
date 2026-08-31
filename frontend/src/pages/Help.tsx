import { useState } from 'react'
import { Icon } from '../components/Icon'
import { SupportModal } from '../components/SupportModal'

/**
 * Страница «Помощь» — краткая инструкция по работе с сервисом (требование п.4в
 * регламента модерации МойСклад) + кнопка «Написать в техподдержку» (тикет в AXIMA ERP).
 * Полная версия инструкции — frontend/public/instruction.html, отдаётся по /instruction.html
 * (эту ссылку указываем в карточке решения МойСклад).
 */
export function HelpPage() {
  const [supportOpen, setSupportOpen] = useState(false)

  return (
    <div className="acc-page">
      <header className="acc-header">
        <h1 className="acc-header__title">Помощь и инструкция</h1>
        <div className="flex-row gap-8" style={{ alignItems: 'center' }}>
          <a
            href="/instruction.html"
            target="_blank"
            rel="noopener noreferrer"
            className="button"
          >
            Полная инструкция
          </a>
          <button
            type="button"
            className="button button--success"
            onClick={() => setSupportOpen(true)}
          >
            <Icon name="support" size={16} style={{ marginRight: 6, verticalAlign: '-3px' }} />
            Написать в техподдержку
          </button>
        </div>
      </header>

      <div className="section" style={{ maxWidth: 820 }}>
        <p className="hint" style={{ marginTop: 0, fontSize: 13 }}>
          Сервис объединяет приёмку, отгрузку, списание и проверку маркированных
          товаров в одном окне: коды маркировки проверяются в «Честном Знаке», а
          документы и позиции подтягиваются и обновляются в «МойСклад».
        </p>

        <h2 style={sectionTitle}>С чего начать</h2>
        <ol style={listStyle}>
          <li>
            Откройте <b>Настройки</b> и подключите <b>МойСклад</b> (при запуске из
            каталога решений подключение уже активно) и <b>Честный Знак</b> (вход по
            электронной подписи через плагин КриптоПро).
          </li>
          <li>
            В Настройках отметьте <b>товарные группы</b>, с которыми работаете, и
            при необходимости укажите <b>ИНН участника оборота</b> (нужен для списания).
          </li>
          <li>
            Выберите <b>режим сканера</b> (Настройки → Сканер): USB (клавиатура) или
            COM-порт. Для COM-порта настройте сканер на <b>9600, 8-N-1, суффикс CR/LF</b>{' '}
            с передачей GS-разделителя — подробности в{' '}
            <a href="/instruction.html" target="_blank" rel="noopener noreferrer">
              полной инструкции
            </a>{' '}
            (§2.4).
          </li>
        </ol>

        <h2 style={sectionTitle}>Приёмка</h2>
        <ol style={listStyle}>
          <li>Раздел <b>Приёмка</b> → выберите товарную группу и поступление из МойСклада.</li>
          <li>Загрузите файл <b>УПД (XML)</b> — коды из документа распознаются автоматически.</li>
          <li>
            Сопоставьте товары, которые не определились по GTIN (панель подбора),
            и нажмите <b>«Отправить приёмку в МС»</b> — коды запишутся в позиции поступления.
          </li>
        </ol>

        <h2 style={sectionTitle}>Отгрузка</h2>
        <ol style={listStyle}>
          <li>Раздел <b>Отгрузка</b> → выберите отгрузку из МойСклада.</li>
          <li>
            Сканируйте коды USB-сканером или вводите вручную. Прогресс по плану —
            в таблице; короба (SSCC) можно раскрыть на штучные коды или отгрузить целиком.
          </li>
          <li>Нажмите <b>«Отгрузить»</b> — коды запишутся в позиции отгрузки в МойСкладе.</li>
        </ol>

        <h2 style={sectionTitle}>Списание</h2>
        <ol style={listStyle}>
          <li>Раздел <b>Списание</b> → соберите коды и выберите причину вывода из оборота.</li>
          <li>
            Подпишите документ электронной подписью — коды выводятся из оборота через
            «Честный Знак» (МойСклад не затрагивается).
          </li>
        </ol>

        <h2 style={sectionTitle}>Проверка марок</h2>
        <p style={paraStyle}>
          Раздел <b>Проверка</b> — отсканируйте или вставьте список кодов, чтобы узнать
          статус, владельца и товар без изменения документов. Результат можно выгрузить в CSV.
        </p>

        <h2 style={sectionTitle}>Нужна помощь?</h2>
        <p style={paraStyle}>
          Если что-то не работает или непонятно — нажмите{' '}
          <button
            type="button"
            className="button button--sm"
            onClick={() => setSupportOpen(true)}
          >
            Написать в техподдержку
          </button>
          . Обращение попадёт напрямую к нашей поддержке; ответ придёт на вашу почту.
        </p>
      </div>

      <SupportModal
        open={supportOpen}
        onClose={() => setSupportOpen(false)}
        page="help"
      />
    </div>
  )
}

const sectionTitle: React.CSSProperties = {
  fontSize: 15,
  fontWeight: 700,
  margin: '20px 0 6px',
}

const listStyle: React.CSSProperties = {
  margin: '0 0 4px',
  paddingLeft: 20,
  fontSize: 13.5,
  lineHeight: 1.7,
}

const paraStyle: React.CSSProperties = {
  fontSize: 13.5,
  lineHeight: 1.7,
  margin: '0 0 4px',
}
