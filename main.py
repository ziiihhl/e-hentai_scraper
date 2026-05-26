import scraper
def main():
    links = scraper.get_links()
    print(links)
    scraper.download_images(links)



if __name__ == "__main__":
    main()
