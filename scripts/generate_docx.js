/**
 * Генерация DOCX файлов по ГОСТ стандартам.
 * Вызывается из Python через subprocess.
 * Вход: JSON из stdin
 * Выход: путь к файлу в stdout
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
  NumberFormat,
  Header,
  Footer,
  TabStopType,
  TabStopPosition,
  convertInchesToTwip,
  convertMillimetersToTwip,
  SectionType,
  PageBreak,
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

  const fontSize = font_size * 2; // docx использует half-points
  const lineSpacing = Math.round(line_spacing * 240); // 240 twips = 1 строка

  // Собираем параграфы
  const docParagraphs = [];

  // ===== Титульный лист =====
  docParagraphs.push(...buildTitlePage(title, work_type, subject, author, university, fontSize));

  // ===== Содержание (заглушка — текстовое) =====
  docParagraphs.push(
    new Paragraph({ pageBreakBefore: true }),
    new Paragraph({
      children: [new TextRun({ text: "СОДЕРЖАНИЕ", bold: true, font: "Times New Roman", size: fontSize })],
      alignment: AlignmentType.CENTER,
      spacing: { after: 200, line: lineSpacing },
    })
  );

  // Генерируем содержание из секций
  let pageEstimate = 3; // титульник + содержание
  for (const section of sections) {
    const label = section.heading || "";
    docParagraphs.push(
      new Paragraph({
        children: [
          new TextRun({ text: `${label}`, font: "Times New Roman", size: fontSize }),
          new TextRun({ text: `\t${pageEstimate}`, font: "Times New Roman", size: fontSize }),
        ],
        spacing: { line: lineSpacing },
        tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
      })
    );
    // Примерная оценка страниц
    const chars = (section.text || "").length;
    pageEstimate += Math.max(1, Math.ceil(chars / 1800));
  }

  // ===== Секции работы =====
  for (const section of sections) {
    const heading = section.heading || "";
    const text = section.text || "";
    const level = section.level || 1;

    // Новая страница перед главой
    if (level === 1) {
      docParagraphs.push(new Paragraph({ pageBreakBefore: true }));
    }

    // Заголовок
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
        alignment: level === 1 ? AlignmentType.CENTER : AlignmentType.LEFT,
        spacing: { before: 200, after: 200, line: lineSpacing },
        indent: level > 1 ? { firstLine: convertMillimetersToTwip(12.5) } : {},
      })
    );

    // Текст — разбиваем на абзацы
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
          alignment: AlignmentType.JUSTIFIED,
          spacing: { line: lineSpacing },
          indent: { firstLine: convertMillimetersToTwip(12.5) },
        })
      );
    }
  }

  // Создаём документ
  const doc = new Document({
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
          // Титульный лист без нумерации — первая секция
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
    const safeName = title.replace(/[^a-zA-Zа-яА-ЯёЁ0-9\s]/g, "").trim().substring(0, 60);
    outPath = path.join(tmpDir, `${safeName || "work"}_${Date.now()}.docx`);
  }

  fs.writeFileSync(outPath, buffer);
  return outPath;
}

function buildTitlePage(title, workType, subject, author, university, fontSize) {
  const paragraphs = [];

  // Университет
  if (university) {
    paragraphs.push(
      new Paragraph({
        children: [
          new TextRun({ text: university, font: "Times New Roman", size: fontSize }),
        ],
        alignment: AlignmentType.CENTER,
        spacing: { after: 400 },
      })
    );
  }

  // Пустое пространство
  for (let i = 0; i < 6; i++) {
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
          new TextRun({ text: `по дисциплине: ${subject}`, font: "Times New Roman", size: fontSize }),
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
        new TextRun({ text: `на тему: «${title}»`, font: "Times New Roman", size: fontSize }),
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
          new TextRun({ text: `Выполнил: ${author}`, font: "Times New Roman", size: fontSize }),
        ],
        alignment: AlignmentType.RIGHT,
      })
    );
  }

  // Пустое пространство до конца страницы
  for (let i = 0; i < 4; i++) {
    paragraphs.push(new Paragraph({ children: [] }));
  }

  // Год
  const year = new Date().getFullYear();
  paragraphs.push(
    new Paragraph({
      children: [
        new TextRun({ text: `${year}`, font: "Times New Roman", size: fontSize }),
      ],
      alignment: AlignmentType.CENTER,
    })
  );

  return paragraphs;
}
