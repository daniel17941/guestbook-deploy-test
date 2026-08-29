export const metadata = { title: "Guestbook Deploy Test" };

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", maxWidth: "40rem", margin: "3rem auto", padding: "0 1.5rem", lineHeight: 1.6 }}>
        {children}
      </body>
    </html>
  );
}
