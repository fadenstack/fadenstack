# Knowledge

A model knows what it learned in training, not your organisation's documents. This page shows the two ways to give it your documents: attach a file to a chat, or let it search your organisation's knowledge base. It says what the model gets in each case, and the limits.

## Two ways in

| | Files you attach | The knowledge base |
|---|---|---|
| Who adds documents | You, in a chat | Your administrator |
| Who can use them | Only you, in that chat | Everyone the collection is open to: everyone in your organisation, or only the people and groups your administrator chose |
| What the model gets | The file's text, or the picture | Passages that match the question, with the document's name |

## Attach a file

![The message box: the paper clip beside the wrench and the puzzle piece, the Model picker and the send button on the right](../images/use/chat-composer.png)

1. In the chat, select the paper clip beside the message box.
2. Pick one or more files. Each shows as a chip above the box; the × removes it.
3. Type your question and send.

The model gets the text of the file along with your question. The file stays with the chat: you can go on asking about it in later messages without attaching it again.

??? info "Which files you can attach, and the limits"
    You can attach these file types, unless your administrator changed the list:

    | Kind | File types |
    |---|---|
    | Documents | PDF, Word (`.docx`), PowerPoint (`.pptx`), Excel (`.xlsx`) |
    | Text | `.txt`, `.md`, `.csv`, `.json`, `.html` |
    | Pictures | `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp` |

    Unless your administrator set others: a file may be up to 10 MB, a chat may have up to 20 files, and all your attached files together may take up to 50 MB. Of a long document, the first 50 pages are read.

??? info "Pictures, scans and long files"
    | | Good to know |
    |---|---|
    | **Pictures** | They help only with a model that can read pictures. Ask your administrator which models can. |
    | **Scans** (documents whose pages are pictures) | They may have no text the server can read. The model then does not get the file. If an answer seems to ignore your file, this may be why. |
    | **Long files** | They may not fit whole into what the model can read at once. The model then searches the file for the parts it needs, or reads its beginning. With the tool mode **Off**, it gets neither. |

    After you send, the chat does not list the files you attached.

## Ask about your organisation's documents

Your administrator can fill a knowledge base with documents for everyone: handbooks, policies, product manuals. You do not add to it yourself.

When your question may depend on those documents, the model searches the knowledge base, reads the passages it finds, and answers from them. It names the document a passage came from. To make sure it looks, say so, for example "Search our documents: how many days of leave carry over?".

??? info "What to expect"
    - The search finds passages, not whole documents. The model can read on around a passage when it needs more of the document.
    - The answer is the model's reading of those passages. Check important answers against the document it names.
    - Only models that can use tools search the knowledge base. If a model never names a document, try another one.
    - With the tool mode **Off** (see [Tools in the chat](tools.md)), the model cannot search.
    - The search does not look in your chats, in other people's attached files, or on the internet.

!!! note "Who sees what"
    Your administrator decides who reads each collection of the knowledge base: everyone, or only some people and groups. Your searches find only the documents you may read. Your own files go in your chat instead.

## When it does not work

| You see | What to do |
|---|---|
| **File type '…' is not allowed.** | Your organisation does not accept that kind of file. Save it as PDF or text, or ask your administrator. |
| **File exceeds maximum size of … MB.** | Make the file smaller, or attach only the part you need. |
| **Maximum of … attachments per session reached.** | Start a new chat for further files. |
| **Total attachment storage would exceed … MB.** | Your attached files fill your space. Deleting a chat does not free it; ask your administrator to remove files you no longer need. |
| The model says it found nothing | The knowledge base may not hold a document on the subject. Ask your administrator what it contains. |
