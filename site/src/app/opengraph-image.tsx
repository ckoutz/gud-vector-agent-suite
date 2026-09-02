import { ImageResponse } from "next/og";

import { site } from "@/lib/site";

export const alt =
  "Güd Vector — If they can't find you, they call someone else.";
export const size = {
  width: 1200,
  height: 630,
};
export const contentType = "image/png";

export default function OpenGraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          background: "#fdfdfd",
          color: "#1c1c1c",
          position: "relative",
          fontFamily: "sans-serif",
        }}
      >
        <div
          style={{
            position: "absolute",
            right: -80,
            top: -80,
            width: 420,
            height: 420,
            borderRadius: 999,
            background: "rgba(252, 112, 4, 0.12)",
          }}
        />
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
            padding: "64px 72px",
            width: "100%",
            height: "100%",
          }}
        >
          <div style={{ display: "flex", flexDirection: "column" }}>
            <div
              style={{
                display: "flex",
                fontSize: 40,
                fontWeight: 700,
                letterSpacing: "-0.04em",
              }}
            >
              Güd Vector
            </div>
            <div
              style={{
                marginTop: 8,
                fontSize: 16,
                letterSpacing: "0.22em",
                textTransform: "uppercase",
                color: "#6b6b6b",
                fontWeight: 600,
              }}
            >
              Consulting Services
            </div>
          </div>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              maxWidth: 900,
            }}
          >
            <div
              style={{
                display: "flex",
                fontSize: 22,
                fontWeight: 600,
                letterSpacing: "0.16em",
                textTransform: "uppercase",
                color: "#FC7004",
              }}
            >
              San Francisco Bay Area
            </div>
            <div
              style={{
                marginTop: 18,
                fontSize: 58,
                fontWeight: 700,
                letterSpacing: "-0.04em",
                lineHeight: 1.08,
              }}
            >
            {site.headline}
            </div>
          </div>
          <div
            style={{
              display: "flex",
              gap: 12,
              fontSize: 20,
              color: "#3d3d3d",
            }}
          >
            <div
              style={{
                display: "flex",
                background: "#ffffff",
                color: "#2c2c2c",
                border: "1px solid #ede4d8",
                borderRadius: 999,
                padding: "10px 18px",
                fontSize: 16,
                fontWeight: 600,
              }}
            >
              Website building for small business
            </div>
            <div
              style={{
                display: "flex",
                background: "#ffffff",
                color: "#2c2c2c",
                border: "1px solid #ede4d8",
                borderRadius: 999,
                padding: "10px 18px",
                fontSize: 16,
                fontWeight: 600,
              }}
            >
              Automating business systems
            </div>
          </div>
        </div>
      </div>
    ),
    { ...size },
  );
}
