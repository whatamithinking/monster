import fire
from requestium import Session, Keys
from ratelimit import limits, sleep_and_retry
from tqdm import tqdm
from urllib import parse
from collections import namedtuple
import types, urllib

SITE = {
	'login'				:	'https://login20.monster.com/Login/SignIn?ch=MONS'
	,'logout'			:	'https://login20.monster.com/SignOut?ch=MONS&intcid=skr_navigation_search_SignOut'
	,'enablespeedapply'	:	'https://job-openings.monster.com/v2/job/speedapplyoptin/'
	,'speedapply'		:	'https://job-openings.monster.com/v2/job/speedapply?jobid={0}'
	,'job'				:	'https://job-openings.monster.com/v2/job/pure-json-view?jobid={0}'
	,'search'			:	{
		'root'			:	'https://www.monster.com/jobs/search/pagination/{type}?isDynamicPage=true&isMKPagination=true'
		,'keywords'		:	'q={0}'
		,'posteddaysago':	'tm={0}'
		,'type'			:	{
			'options'	:	{
				'part_time'	:	'Part-Time'
				,'full_time':	'Full-Time'
			}
		}
	}
}

QUICK_APPLY_KEYWORDS = [
	'ApplyOnline'
	,'ApplyWithMonster'
]

RECRUITING_AGENCY_KEYWORDS = [
	'staffing'
	,'consulting'
	,'consultants'
	,'recruiting'
	,'recruiter'
	,'recruitment'
	,'group'
	,'employment'
	,'sourcing'
	,'resourcing'
	,'talent'
	,'workforce planning'
	,'force'
	,'hire'
]

SearchResult = namedtuple( 'SearchResult', "ApplyLink, DetailsLink" )

class Monster():
	api_throttle_secs = 3

	def __init__( self ):
		self._session = Session(
				webdriver_path=''
				,browser='chrome'
				,default_timeout=15
				,webdriver_options={
						'arguments' : [ 'headless' ]
					}
			)

	@sleep_and_retry
	@limits( calls=1, period=api_throttle_secs )
	def apply( self, job_link ):
		'''Apply to the job at the given job link for Monster.com.

		Args:
			job_link (str_or_SearchResult): the speed apply link for the job to apply to.

		Returns:
			bool: True if successful, False otherwise.
		'''
		if isinstance( job_link, SearchResult ):
			job_link = job_link.ApplyLink
		apply_result = self._session.get( job_link )
		if apply_result.status_code == 200:
			if apply_result.json()['success'] == True:
				return True
			else:
				print( job_link )
				print( apply_result.json() )
		return False

	def batchApply( self, job_links ):
		''' Apply to all jobs in the list of job links given
		
		Args:
			job_links (list_or_generator): List, tuple, or generator of job links
		
		Returns:
			jobs_applied_to (int): The number of jobs applied to successfully
		'''
		jobs_quantity = 0
		quantity_applied_to = 0
		if not isinstance( job_links, types.GeneratorType ):
			jobs_quantity = len( job_links )
		progress_bar = tqdm(
			total=jobs_quantity
			,desc='Applying'
			,unit='Jobs' 
		)
		for job_link in job_links:
			if isinstance( job_links, types.GeneratorType ):
				progress_bar.total += 1
			if self.apply( job_link ):
				progress_bar.update( 1 )
		jobs_applied_to = progress_bar.n
		return jobs_applied_to

	@sleep_and_retry
	@limits( calls=1, period=api_throttle_secs )
	def login( self, email, password ):
		'''Login to the Monster.com job board site.

		Args:
			email (str): Email address for logging into Monster.com.
			password (str): Password corresponding to email address to
				login to Monster.com job board site.

		Returns:
			bool: True if successful, False otherwise.
		'''

		# GOTO LOGIN PAGE TO CHECK IF AVAILABLE & GET COOKIES
		login_page = self._session.get( SITE['login'] )
		if login_page.status_code != 200:
			raise Exception( 'ERROR: COULD NOT GET LOGIN PAGE FOR MONSTER.COM : ' + SITE['login'] )

		# BUILD FORM DATA
		login_data = {
			'AreCookiesEnabled'			:	True
			,'EmailAddress'				: 	email
			,'IsComingFromProtectedView':	False
			,'IsKeepMeLoggedInEnabled'	:	True
			,'Password'					:	password
			,'PersistLogin'				:	True
		}
		request_verification_token = \
			login_page.xpath('//input[@name="__RequestVerificationToken"]/@value').extract()[0]
		login_data.update( { '__RequestVerificationToken' : request_verification_token } )

		# LOGIN
		login_result = self._session.post( SITE['login'], data=login_data )
		if login_result.status_code == 200:
			return True
		else:
			return False

	@sleep_and_retry
	@limits( calls=1, period=api_throttle_secs )
	def getJobDetails( self, job_link ):
		''' Get dictionary of details of the job, such as title and description.

		Args:
			job_link (str or int): Either a url containing the job id in the format
				of jobid={}, such as the apply link or the job page link. Or, directly
				supply the job id if it is available.

		Returns:
			job_dict (dict): Dictionary of the job link, job title, company name,
				job address, and job description.
		'''
		job_link = str( job_link )
		if not 'jobid' in job_link:
			job_id = job_link
		else:
			job_id = parse.parse_qs( parse.urlparse( job_link ).query )['jobid'][0]
		job_url = SITE[ 'job' ].format( job_id )
		job_page = self._session.get( job_url )
		job_json = job_page.json()
		job_description = job_json[ 'jobDescription' ]
		job_title = job_json[ 'companyInfo' ][ 'companyHeader' ]
		company_name = job_json[ 'companyInfo' ][ 'name' ]
		job_address = job_json[ 'companyInfo' ][ 'jobLocation' ]
		job_dict = {
			'job_link'          :   job_link
            ,'job_title'        :   job_title
            ,'job_address'      :   job_address
            ,'company_name'     :   company_name
            ,'job_description'  :   job_description
		}
		return job_dict

	def search( self, quantity=25, filter_out_recruiting_agencies=True, **kwargs ):
		''' Search Monster.com with the given filters and yield job links.
		
		Args:
			quantity (int): The max number of results to return.
			kwargs (dict): Dictionary of filters, such as keywords, 
				type (full_time,part_time), and posteddaysago.
				
		Returns:
			SearchResult (namedtuple): generator of named tuples, each
				containing an ApplyLink and a DetailsLink. The ApplyLink,
				when followed, will apply for the job automatically. The 
				Details link will return json data about the job.
		'''
		search_url = SITE['search']['root']
		
		# HANDLE SPECIAL CASE OF JOB TYPE, WHICH MUST PRECEED QUERY
		job_type_value = ''
		if 'type' in kwargs:
			job_type = kwargs['type']
			options = SITE['search']['type']['options']
			job_type_value = options[job_type] if job_type in options else ''
			kwargs.pop( 'type' )
		search_url = search_url.format(
			type=urllib.parse.quote_plus( job_type_value )
		)
			
		# FORMAT URL WITH REMAINING FILTERS
		for search_field, search_value in kwargs.items():
			if search_field in SITE['search']:
				if isinstance( SITE['search'][search_field], dict ):
					options = SITE['search'][search_field]['options']
					if search_value in options:
						options_value = options[search_value]
						search_url += '+' + urllib.parse.quote_plus( options_value )
				else:
					search_format = SITE['search'][search_field]
					search_url += \
						'&{0}'.format(search_format.format(urllib.parse.quote_plus(search_value)))

		@sleep_and_retry
		@limits( calls=1, period=self.api_throttle_secs )
		def getPage( page ):
			paged_search_url = search_url + '&page=' + str( page )
			search_page = self._session.get( paged_search_url )
			return search_page
		
		# GET AND PROCESS RETURNED JSON
		quantity_returned = 0
		page = 1
		while quantity_returned < quantity:
			search_page = getPage( page )
			if search_page.status_code != 200:
				break
			search_json = search_page.json()
			for app_dict in search_json:
				if app_dict['JobID'] != 0 and app_dict['ApplyType'] != None:			# filter jobs that are missing data / poorly formatted
					if app_dict['IsAppliedJob'] == False:								# filter jobs already applied to
						if any( x in app_dict['ApplyType'] \
							for x in QUICK_APPLY_KEYWORDS ):							# filter to include quick apply jobs only
							if not any( x.lower() in app_dict['Company']['Name'].lower() \
								for x in RECRUITING_AGENCY_KEYWORDS ) or \
								not filter_out_recruiting_agencies:						# filter jobs from recruiting agencies
								job_id = app_dict['JobID']
								apply_url = SITE['speedapply'].format( job_id )
								details_url = SITE['job'].format( job_id )
								search_result = SearchResult( apply_url, details_url )
								quantity_returned += 1
								yield search_result
				if quantity_returned >= quantity:
					break
			page += 1

if __name__ == '__main__':
	fire.Fire( Monster )
