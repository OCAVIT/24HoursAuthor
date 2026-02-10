/**
 * Генерация DOCX файлов по ГОСТ стандартам.
 * Вызывается из Python через subprocess.
 * Вход: JSON из stdin
 * Выход: путь к файлу в stdout
 *
 * Улучшения:
 * - Заголовки используют HeadingLevel (Heading1/Heading2) для правильных стилей
 * - Содержание генерируется из заголовков с оценкой номеров страниц
 * - Параграфы имеют стиль "Normal" с корректным форматированием
 * - TOC с точками-заполнителями между названием и номером страницы
 */

const fs = require("fs");
const path = require("path");
const {
  Document,
  Packer,
  Paragraph,
  TextRun,
  HeadingLevel,
  AlignmentType,
  PageNumber,
  Header,
  Footer,
  TabStopType,
  TabStopPosition,
  convertMillimetersToTwip,
  LevelFormat,
  TableOfContents,
  StyleLevel,
} = require("docx");

// Читаем JSON из stdin
let inputData = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  inputData += chunk;
});

process.stdin.on("end", async () => {
  try {
    const data = JSON.parse(inputData);
    const outputPath = await generateDocx(data);
    process.stdout.write(outputPath);
  } catch (err) {
    process.stderr.write(`Error: ${err.message}\n${err.stack}`);
    process.exit(1);
  }
});

// Символов на страницу при Times New Roman 14pt, 1.5 интервал
const CHARS_PER_PAGE = 1800;

/** Проверить, является ли заголовок секцией библиографии. */
function isBibliography(heading) {
  const h = (heading || "").toLowerCase();
  return (
    h.includes("список литературы") ||
    h.includes("библиограф") ||
    h.includes("список источников")
  );
}

async function generateDocx(data) {
  const {
    title = "Без названия",
    work_type = "Реферат",
    subject = "",
    author = "",
    university = "",
    sections = [],
    font_size = 14,
    line_spacing = 1.5,
    output_path = "",
  } = data;

  const fontSize = font_size * 2; // docx uses half-points
  const lineSpacing = Math.round(line_spacing * 240); // 240 twips = 1 line

  // Собираем параграфы
  const docParagraphs = [];

  // ===== Титульный лист =====
  docParagraphs.push(
    ...buildTitlePage(title, work_type, subject, author, university, fontSize)
  );

  // ===== Содержание =====
  const hasTOC = sections.length > 1;
  if (hasTOC) {
    docParagraphs.push(
      new Paragraph({ pageBreakBefore: true }),
      new Paragraph({
        children: [
          new TextRun({
            text: "СОДЕРЖАНИЕ",
            bold: true,
            font: "Times New Roman",
            size: fontSize,
          }),
        ],
        alignment: AlignmentType.CENTER,
        spacing: { after: 300, line: lineSpacing },
      })
    );

    // Генерируем ручное содержание с оценками номеров страниц
    let pageEstimate = 3; // титульник + содержание
    for (const section of sections) {
      const label = section.heading || "";
      const level = section.level || 1;

      // Оценка страниц для этого раздела
      const chars = (section.text || "").length;
      const sectionPages = Math.max(1, Math.ceil(chars / CHARS_PER_PAGE));

      // Пропускаем секции без заголовка
      if (!label.trim()) {
        pageEstimate += sectionPages;
        continue;
      }

      // Формат: "Название ...... N"
      // Отступ для level 2
      const indent =
        level > 1 ? { left: convertMillimetersToTwip(10) } : {};

      docParagraphs.push(
        new Paragraph({
          children: [
            new TextRun({
              text: label,
              font: "Times New Roman",
              size: fontSize,
            }),
            new TextRun({
              text: "\t",
              font: "Times New Roman",
              size: fontSize,
            }),
            new TextRun({
              text: `${pageEstimate}`,
              font: "Times New Roman",
              size: fontSize,
            }),
          ],
          spacing: { line: lineSpacing },
          tabStops: [
            {
              type: TabStopType.RIGHT,
              position: TabStopPosition.MAX,
              leader: "dot",
            },
          ],
          indent,
        })
      );

      pageEstimate += sectionPages;
    }
  }

  // ===== Секции работы =====
  for (const section of sections) {
    const heading = section.heading || "";
    const text = section.text || "";
    const level = section.level || 1;
    const isBib = isBibliography(heading);

    // Новая страница перед главами (level 1)
    const pageBreak = level === 1;

    // Заголовок с HeadingLevel для правильного стиля
    const headingLevel =
      level === 1 ? HeadingLevel.HEADING_1 : HeadingLevel.HEADING_2;

    if (heading) {
      docParagraphs.push(
        new Paragraph({
          children: [
            new TextRun({
              text: heading,
              bold: true,
              font: "Times New Roman",
              size: fontSize,
            }),
          ],
          heading: headingLevel,
          alignment: level === 1 ? AlignmentType.CENTER : AlignmentType.LEFT,
          spacing: { before: 200, after: 200, line: lineSpacing },
          indent:
            level > 1 ? { firstLine: convertMillimetersToTwip(12.5) } : {},
          pageBreakBefore: pageBreak,
        })
      );
    }

    if (isBib) {
      // Библиография: каждая строка — отдельная запись, без абзацного отступа
      const lines = text.split(/\n/);
      for (const line of lines) {
        if (!line.trim()) continue;
        docParagraphs.push(
          new Paragraph({
            children: [
              new TextRun({
                text: line.trim(),
                font: "Times New Roman",
                size: fontSize,
              }),
            ],
            style: "Normal",
            alignment: AlignmentType.JUSTIFIED,
            spacing: { line: lineSpacing },
            // Библиография: выступающий отступ (hanging indent)
            indent: {
              left: convertMillimetersToTwip(12.5),
              hanging: convertMillimetersToTwip(12.5),
            },
          })
        );
      }
    } else {
      // Обычный текст — разбиваем на абзацы
      const paragraphs = text.split(/\n\n+/);
      for (const para of paragraphs) {
        if (!para.trim()) continue;
        docParagraphs.push(
          new Paragraph({
            children: [
              new TextRun({
                text: para.trim(),
                font: "Times New Roman",
                size: fontSize,
              }),
            ],
            style: "Normal",
            alignment: AlignmentType.JUSTIFIED,
            spacing: { line: lineSpacing },
            indent: { firstLine: convertMillimetersToTwip(12.5) },
          })
        );
      }
    }
  }

  // Стили документа: переопределяем Heading1, Heading2 и Normal
  const styles = {
    default: {
      document: {
        run: {
          font: "Times New Roman",
          size: fontSize,
        },
        paragraph: {
          spacing: { line: lineSpacing },
        },
      },
      heading1: {
        run: {
          font: "Times New Roman",
          size: fontSize,
          bold: true,
        },
        paragraph: {
          alignment: AlignmentType.CENTER,
          spacing: { before: 240, after: 240, line: lineSpacing },
        },
      },
      heading2: {
        run: {
          font: "Times New Roman",
          size: fontSize,
          bold: true,
        },
        paragraph: {
          alignment: AlignmentType.LEFT,
          spacing: { before: 200, after: 200, line: lineSpacing },
          indent: { firstLine: convertMillimetersToTwip(12.5) },
        },
      },
    },
  };

  // Создаём документ
  const doc = new Document({
    styles,
    sections: [
      {
        properties: {
          page: {
            margin: {
              top: convertMillimetersToTwip(20),
              bottom: convertMillimetersToTwip(20),
              left: convertMillimetersToTwip(30),
              right: convertMillimetersToTwip(15),
            },
          },
          titlePage: true,
        },
        headers: {
          default: new Header({ children: [] }),
          first: new Header({ children: [] }),
        },
        footers: {
          default: new Footer({
            children: [
              new Paragraph({
                children: [
                  new TextRun({
                    children: [PageNumber.CURRENT],
                    font: "Times New Roman",
                    size: fontSize,
                  }),
                ],
                alignment: AlignmentType.CENTER,
              }),
            ],
          }),
          first: new Footer({ children: [] }), // Без нумерации на титульнике
        },
        children: docParagraphs,
      },
    ],
  });

  // Генерируем файл
  const buffer = await Packer.toBuffer(doc);

  // Определяем путь для сохранения
  let outPath = output_path;
  if (!outPath) {
    const tmpDir = path.join(process.cwd(), "tmp", "generated");
    if (!fs.existsSync(tmpDir)) {
      fs.mkdirSync(tmpDir, { recursive: true });
    }
    const safeName = title
      .replace(/[^a-zA-Zа-яА-ЯёЁ0-9\s]/g, "")
      .trim()
      .substring(0, 60);
    outPath = path.join(tmpDir, `${safeName || "work"}_${Date.now()}.docx`);
  }

  fs.writeFileSync(outPath, buffer);
  return outPath;
}

function buildTitlePage(title, workType, subject, author, university, fontSize) {
  // Типы без титульного листа
  const noTitleTypes = [
    "Копирайтинг",
    "Набор текста",
    "Перевод",
    "Повышение уникальности текста",
    "Гуманизация работы",
  ];
  if (noTitleTypes.includes(workType)) {
    return [];
  }

  const paragraphs = [];

  // Министерство (для ВКР/дипломных/курсовых)
  const academicTypes = [
    "Курсовая работа",
    "Дипломная работа",
    "Выпускная квалификационная работа (ВКР)",
    "Монография",
    "Научно-исследовательская работа (НИР)",
    "Индивидуальный проект",
  ];
  if (academicTypes.includes(workType)) {
    paragraphs.push(
      new Paragraph({
        children: [
          new TextRun({
            text: "МИНИСТЕРСТВО НАУКИ И ВЫСШЕГО ОБРАЗОВАНИЯ РОССИЙСКОЙ ФЕДЕРАЦИИ",
            font: "Times New Roman",
            size: fontSize - 2,
          }),
        ],
        alignment: AlignmentType.CENTER,
        spacing: { after: 100 },
      })
    );
  }

  // Университет
  if (university) {
    paragraphs.push(
      new Paragraph({
        children: [
          new TextRun({
            text: university,
            font: "Times New Roman",
            size: fontSize,
          }),
        ],
        alignment: AlignmentType.CENTER,
        spacing: { after: 400 },
      })
    );
  }

  // Пустое пространство
  const spaceBefore = academicTypes.includes(workType) ? 4 : 6;
  for (let i = 0; i < spaceBefore; i++) {
    paragraphs.push(new Paragraph({ children: [] }));
  }

  // Тип работы
  paragraphs.push(
    new Paragraph({
      children: [
        new TextRun({
          text: workType.toUpperCase(),
          bold: true,
          font: "Times New Roman",
          size: fontSize + 4,
        }),
      ],
      alignment: AlignmentType.CENTER,
      spacing: { after: 200 },
    })
  );

  // Предмет
  if (subject) {
    paragraphs.push(
      new Paragraph({
        children: [
          new TextRun({
            text: `по дисциплине: ${subject}`,
            font: "Times New Roman",
            size: fontSize,
          }),
        ],
        alignment: AlignmentType.CENTER,
        spacing: { after: 200 },
      })
    );
  }

  // Тема
  paragraphs.push(
    new Paragraph({
      children: [
        new TextRun({
          text: `на тему: «${title}»`,
          font: "Times New Roman",
          size: fontSize,
        }),
      ],
      alignment: AlignmentType.CENTER,
      spacing: { after: 400 },
    })
  );

  // Пустое пространство
  for (let i = 0; i < 6; i++) {
    paragraphs.push(new Paragraph({ children: [] }));
  }

  // Автор (справа)
  if (author) {
    paragraphs.push(
      new Paragraph({
        children: [
          new TextRun({
            text: `Выполнил: ${author}`,
            font: "Times New Roman",
            size: fontSize,
          }),
        ],
        alignment: AlignmentType.RIGHT,
      })
    );
  }

  if (academicTypes.includes(workType)) {
    paragraphs.push(
      new Paragraph({
        children: [
          new TextRun({
            text: "Научный руководитель: _________________",
            font: "Times New Roman",
            size: fontSize,
          }),
        ],
        alignment: AlignmentType.RIGHT,
        spacing: { before: 100 },
      })
    );
  }

  // Пустое пространство до конца страницы
  for (let i = 0; i < 4; i++) {
    paragraphs.push(new Paragraph({ children: [] }));
  }

  // Город и год
  const year = new Date().getFullYear();
  paragraphs.push(
    new Paragraph({
      children: [
        new TextRun({
          text: `${year}`,
          font: "Times New Roman",
          size: fontSize,
        }),
      ],
      alignment: AlignmentType.CENTER,
    })
  );

  return paragraphs;
}
