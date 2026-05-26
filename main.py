from scraper import *
def main():
    gallery_url = input_gallery_url()
    session = build_session()
    title,cnt = get_links_and_download(session, gallery_url)
    console.print(f"成功下载 {cnt} 张图片，保存到: {SAVE_DIR / title}")
if __name__ == "__main__":
    main()
