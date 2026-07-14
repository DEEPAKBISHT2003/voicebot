import * as pdfjs from 'pdfjs-dist';

import pdfjsWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

pdfjs.GlobalWorkerOptions.workerSrc = pdfjsWorker;

export const extractTextFromPDF = async (arrayBuffer: ArrayBuffer): Promise<string> => {
  const loadingTask = pdfjs.getDocument({ data: new Uint8Array(arrayBuffer) });
  const pdf = await loadingTask.promise;
  let text = '';
  
  for (let i = 1; i <= pdf.numPages; i++) {
    const page = await pdf.getPage(i);
    const textContent = await page.getTextContent();
    // Concatenate text strings from items
    const pageText = textContent.items
      .map((item: any) => item.str)
      .join(' ');
    text += pageText + '\n';
  }
  
  return text.trim();
};
